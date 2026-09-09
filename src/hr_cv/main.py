"""FastAPI 后端：持久化会话（SQLite）+ 延迟解析（发送时才 OCR）+ 流式对话（SSE，含思维链）。

流程：
1. 上传只保存原始文件（不解析），秒级完成并入库；
2. 发送问题时，先逐份解析（线程池 OCR），SSE 依次推 parse 事件；
3. 之后推 meta（引用映射）与 stage（思考中）；
4. LLM 流式阶段按 DeepSeek 官方协议分双通道：reasoning_content -> reasoning 事件，
   content -> delta 事件；结束后把消息（含思维链与耗时）追加入库。

日志策略：所有异常一律 logger.exception 记录完整原始堆栈（不走兜底改写），
向客户端透出的也是原始 `类型: 消息`。
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import signal
import subprocess
import sys
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from hr_cv import llm, storage
from hr_cv.parser import SUPPORTED_EXTENSIONS, parse_file, quick_validate
from hr_cv.config import APP_VERSION, config_summary, settings, setup_logging

setup_logging()
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="AI简历筛选助手")

# 启动时从 SQLite 加载全部会话：sid -> session dict
_SESSIONS: dict[str, dict] = storage.load_all()

# 启动时加载页面保存的 LLM 接入配置（优先于 .env）
llm.set_runtime_config(storage.get_llm_overrides())


def _mask_key(key: str) -> str:
    if not key:
        return "(未设置)"
    return f"{key[:6]}****{key[-4:]}" if len(key) > 12 else "****"


def _legacy_json_to_session(raw: dict) -> dict:
    """旧版 JSON 会话结构 -> 内存会话结构（仅迁移器使用）。"""
    import base64

    resumes = {
        rid: {
            "id": r["id"],
            "filename": r["filename"],
            "format": r["format"],
            "data": base64.b64decode(r["data_b64"]),
            "parsed": r.get("parsed"),
        }
        for rid, r in raw.get("resumes", {}).items()
    }
    return {
        "id": raw["id"],
        "title": raw.get("title", "新会话"),
        "created_at": raw.get("created_at", time.time()),
        "updated_at": raw.get("updated_at", time.time()),
        "resumes": resumes,
        "messages": raw.get("messages", []),
    }


def _migrate_legacy_json() -> None:
    """一次性迁移旧版 JSON 会话到 SQLite；成功/跳过的文件归档到 sessions_json_backup/。"""
    legacy_dir = settings.data_dir / "sessions"
    if not legacy_dir.is_dir():
        return
    backup_dir = settings.data_dir / "sessions_json_backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for f in sorted(legacy_dir.glob("*.json")):
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            sid = raw["id"]
            if sid in _SESSIONS:
                logger.info("迁移跳过（SQLite 已存在该会话）: %s", sid)
            else:
                session = _legacy_json_to_session(raw)
                _SESSIONS[sid] = session
                storage.save(session)
                logger.info(
                    "迁移完成: %s | 标题=%s | 简历=%d | 消息=%d",
                    sid, session["title"], len(session["resumes"]), len(session["messages"]),
                )
            f.rename(backup_dir / f.name)
            logger.info("已归档: %s -> %s", f.name, backup_dir / f.name)
        except Exception:
            # 损坏文件：记录完整堆栈，改名保留在备份目录（加 .corrupt 后缀），不阻断其余迁移
            logger.exception("迁移失败（原始文件损坏）: %s", f)
            try:
                f.rename(backup_dir / (f.name + ".corrupt"))
            except OSError:
                pass


_migrate_legacy_json()


@app.middleware("http")
async def no_cache_dev_assets(request, call_next):
    """开发期前端资源不缓存，避免浏览器用旧 JS 调新接口。"""
    response = await call_next(request)
    if request.url.path in {"/", "/index.html", "/app.js", "/style.css"}:
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """未捕获异常：完整堆栈进日志，向客户端返回原始 type/message。"""
    logger.exception("未捕获异常 %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


@app.get("/api/health")
async def health():
    """健康检查：返回应用标识，供桌面单实例识别与前端服务横幅重试共用。"""
    return {"app": "hr-cv", "version": APP_VERSION, "ok": True}


def _get_or_create(sid: str) -> dict:
    s = _SESSIONS.get(sid)
    if not s:
        s = storage.new_session(sid)
        _SESSIONS[sid] = s
        storage.save(s)
    return s


@app.post("/api/upload")
async def upload_files(
    session_id: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File(...)],
):
    """批量上传简历（.pdf / .md）：只存原始文件，不解析（延迟到发送时）。"""
    if not files:
        raise HTTPException(status_code=400, detail="未收到文件")

    session = _get_or_create(session_id)
    store = session["resumes"]
    created: list[dict] = []
    errors: list[dict] = []
    for f in files:
        ext = "." + f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in SUPPORTED_EXTENSIONS:
            errors.append({"filename": f.filename, "error": "仅支持 PDF 与 MD 格式"})
            continue
        data = await f.read()
        # 轻量校验：PDF 必须能被打开（不 OCR），坏文件立即标坏，前端即时反馈
        err = await run_in_threadpool(quick_validate, f.filename, data)
        rid = uuid.uuid4().hex
        record = {
            "id": rid,
            "filename": f.filename,
            "format": ext.lstrip("."),
            "data": data,
            "parsed": {"failed": err} if err else None,
        }
        # 同名文件重新上传时移除旧记录
        for old_id in [i for i, r in store.items() if r["filename"] == f.filename]:
            store.pop(old_id, None)
        store[rid] = record
        item = {k: record[k] for k in ("id", "filename", "format")}
        if err:
            item["failed"] = err
            logger.warning("上传校验失败: session=%s file=%s 原因=%s", session_id, f.filename, err)
        created.append(item)
        logger.info("上传: session=%s file=%s size=%d 字节", session_id, f.filename, len(data))

    storage.save(session)
    logger.info("上传批次完成: session=%s 成功=%d 失败=%d 总数=%d",
                session_id, len(created), len(errors), len(store))
    return {"created": created, "errors": errors, "total": len(store)}


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream_events(session: dict, prompt: str, resumes: list[dict]):
    """SSE 事件流：parse* -> meta -> stage -> reasoning* -> delta* -> [error] -> DONE。"""
    session_id = session["id"]
    logger.info("对话开始: session=%s 提问长度=%d 简历数=%d", session_id, len(prompt), len(resumes))

    # 阶段 1：并发解析未解析的简历（tesseract 为子进程调用，线程并发安全；事件循环保持空闲）
    to_parse = [r for r in resumes if not r["parsed"]]
    done = 0
    t_parse0 = time.monotonic()
    if to_parse:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(parse_file, r["filename"], r["data"]): r for r in to_parse}

            async def _await_parse(r: dict, fut):
                try:
                    return r, await asyncio.wrap_future(fut), None
                except Exception as e:  # noqa: BLE001
                    return r, None, e

            for coro in asyncio.as_completed([_await_parse(r, fut) for fut, r in futures.items()]):
                r, parsed, err = await coro
                if err is not None:
                    logger.error("解析失败: session=%s file=%s", session_id, r["filename"],
                                 exc_info=(type(err), err, err.__traceback__))
                    r["parsed"] = {"failed": str(err)}
                    yield _sse({"type": "parse_error", "id": r["id"], "filename": r["filename"],
                                "error": f"解析失败：{err}"})
                    continue
                r["parsed"] = {"content": parsed.content, "method": parsed.method, "pages": parsed.pages}
                done += 1
                logger.info("解析完成: session=%s file=%s method=%s 字数=%d",
                            session_id, r["filename"], parsed.method, len(parsed.content))
                yield _sse({"type": "parse", "id": r["id"], "filename": r["filename"],
                            "method": parsed.method, "done": done, "total": len(to_parse)})
        logger.info("解析阶段完成: session=%s 文件=%d 并发=4 耗时=%.1fs",
                    session_id, len(to_parse), time.monotonic() - t_parse0)

    usable = [r for r in resumes if r["parsed"] and "content" in r["parsed"]]
    if not usable:
        logger.warning("无可用简历: session=%s", session_id)
        yield _sse({"type": "error", "text": "没有可用的简历（请先上传，或检查解析失败原因）"})
        yield "data: [DONE]\n\n"
        return

    # 阶段 2：引用映射（序号 -> 简历）
    refs = [
        {"index": i + 1, "id": r["id"], "filename": r["filename"], "method": r["parsed"]["method"]}
        for i, r in enumerate(usable)
    ]
    yield _sse({"type": "meta", "count": len(usable), "resumes": refs})

    # 阶段 3：思考中（LLM 异步流式：reasoning -> delta）
    t_start = time.monotonic()
    yield _sse({"type": "stage", "text": "思考中…"})

    view = [{**{k: r[k] for k in ("id", "filename")}, **r["parsed"]} for r in usable]
    reasoning_text = ""
    content_text = ""
    reasoning_seconds = None
    try:
        async for kind, piece in llm.stream_match(prompt, view):
            if kind == "reasoning":
                reasoning_text += piece
                yield _sse({"type": "reasoning", "text": piece})
            elif kind == "delta":
                if not content_text:
                    reasoning_seconds = round(time.monotonic() - t_start, 1)
                content_text += piece
                yield _sse({"type": "delta", "text": piece})
            else:  # llm 层兜底 error（原始文案）
                yield _sse({"type": "error", "text": piece})
    except Exception as e:  # noqa: BLE001
        logger.exception("LLM 流式调用失败: session=%s", session_id)
        yield _sse({"type": "error", "text": f"{type(e).__name__}: {e}"})

    logger.info(
        "对话结束: session=%s 思考耗时=%ss 思维链=%d字 回答=%d字",
        session_id, reasoning_seconds, len(reasoning_text), len(content_text),
    )

    # 持久化：user + assistant 消息（有实际回答才记录）
    if content_text:
        session["messages"].append({"role": "user", "content": prompt})
        session["messages"].append({
            "role": "assistant",
            "content": content_text,
            "refs": refs,
            "reasoning": reasoning_text,
            "reasoning_seconds": reasoning_seconds,
        })
        if session["title"] == "新会话":
            session["title"] = prompt[:20]
        storage.save(session)
    yield "data: [DONE]\n\n"


@app.post("/api/chat")
async def chat(body: dict):
    """根据用户要求 + 会话内简历，先解析再流式返回候选人匹配结果。"""
    session_id = body.get("session_id") or ""
    prompt = (body.get("message") or "").strip()
    resume_ids = body.get("resume_ids") or []
    if not prompt:
        raise HTTPException(status_code=400, detail="请输入你的问题")

    # LLM 配置预检：未配置则不打 OCR、不发请求；detail 前缀 `LLM_NOT_CONFIGURED:`
    # 是与前端约定的机器可读契约（前端据此打开「模型接入」弹窗）
    if not llm.is_configured():
        raise HTTPException(
            status_code=400,
            detail="LLM_NOT_CONFIGURED:尚未配置模型接入，请打开「模型接入」填写 Base URL、API Key 与模型 ID",
        )

    session = _SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=400, detail="会话不存在，请刷新页面")
    store = session["resumes"]
    if resume_ids:
        resumes = [store[i] for i in resume_ids if i in store]
    else:
        resumes = list(store.values())
    if not resumes:
        raise HTTPException(status_code=400, detail="请先上传简历")

    return StreamingResponse(
        _stream_events(session, prompt, resumes),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/resume/{rid}")
async def get_resume(rid: str, session_id: str = ""):
    """前端点击引用时，返回该简历的解析文本。"""
    session = _SESSIONS.get(session_id, {})
    record = session.get("resumes", {}).get(rid)
    if not record:
        raise HTTPException(status_code=404, detail="简历不存在")
    parsed = record.get("parsed")
    if not parsed or "content" not in parsed:
        raise HTTPException(status_code=409, detail="该简历尚未解析（发送问题后自动解析）")
    return {
        "id": record["id"],
        "filename": record["filename"],
        "format": record["format"],
        "method": parsed["method"],
        "content": parsed["content"],
    }


@app.post("/api/clear")
async def clear_session(body: dict):
    """清空指定会话的简历与消息（会话本身保留）。"""
    session_id = body.get("session_id") or ""
    session = _SESSIONS.get(session_id)
    if not session:
        return {"cleared": False, "removed": 0}
    removed = len(session["resumes"]) + len(session["messages"])
    session["resumes"] = {}
    session["messages"] = []
    session["title"] = "新会话"
    storage.save(session)
    logger.info("会话已清空: session=%s 清除项=%d", session_id, removed)
    return {"cleared": True, "removed": removed}


# ---------------- 会话管理 API ----------------

@app.get("/api/sessions")
async def list_sessions():
    """会话列表，按最近更新降序。"""
    items = [
        {
            "id": s["id"],
            "title": s["title"],
            "created_at": s["created_at"],
            "updated_at": s["updated_at"],
            "resume_count": len(s["resumes"]),
            "message_count": len(s["messages"]),
        }
        for s in _SESSIONS.values()
    ]
    return sorted(items, key=lambda x: x["updated_at"], reverse=True)


@app.post("/api/sessions")
async def create_session():
    """新建空会话。"""
    sid = uuid.uuid4().hex
    session = storage.new_session(sid)
    _SESSIONS[sid] = session
    storage.save(session)
    logger.info("新建会话: %s", sid)
    return {"id": sid}


@app.get("/api/sessions/{sid}")
async def get_session(sid: str):
    """会话全量详情（不含页面图片与文件字节）。"""
    session = _SESSIONS.get(sid)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    resumes = []
    for r in session["resumes"].values():
        parsed = r.get("parsed") or {}
        resumes.append({
            "id": r["id"],
            "filename": r["filename"],
            "format": r["format"],
            "method": parsed.get("method"),
            "failed": parsed.get("failed"),
            "content": parsed.get("content"),
        })
    return {
        "id": session["id"],
        "title": session["title"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "resumes": resumes,
        "messages": session["messages"],
    }


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    """删除会话（内存 + SQLite）。"""
    if sid not in _SESSIONS:
        raise HTTPException(status_code=404, detail="会话不存在")
    _SESSIONS.pop(sid, None)
    storage.delete(sid)
    logger.info("删除会话: %s", sid)
    return {"deleted": True}


# ---------------- 日志：客户端上报 + 一键导出 ----------------

@app.post("/api/client-log")
async def client_log(body: dict):
    """前端错误上报：原文写入 app.log（[CLIENT] 前缀），不做任何改写。"""
    payload = json.dumps(body, ensure_ascii=False)[:65536]
    logger.error("[CLIENT] %s", payload)
    return {"ok": True}


@app.get("/api/logs/export")
async def export_logs():
    """一键导出日志：app.log 及轮转文件 + meta.txt（含脱敏配置），zip 下载。"""
    logs_dir = settings.data_dir / "logs"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        meta = "\n".join([
            f"app_version={APP_VERSION}",
            f"exported_at={time.strftime('%Y-%m-%d %H:%M:%S')}",
            config_summary(),
        ])
        z.writestr("meta.txt", meta)
        if logs_dir.is_dir():
            for f in sorted(logs_dir.glob("app.log*")):
                z.write(f, arcname=f.name)
    buf.seek(0)
    stamp = time.strftime("%Y%m%d-%H%M")
    logger.info("导出日志 zip（%d 字节）", buf.getbuffer().nbytes)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=hr-cv-logs-{stamp}.zip"},
    )


# ---------------- LLM 接入配置与连通性测试 ----------------

@app.get("/api/llm/config")
async def get_llm_config():
    """当前生效配置（key 脱敏回显，永不明文）。"""
    o = llm.runtime_overrides()
    base_url = o.get("base_url") or settings.base_url
    model = o.get("model") or settings.model
    key = o.get("api_key") or settings.api_key
    effort = o.get("reasoning_effort") or settings.reasoning_effort
    return {
        "base_url": base_url,
        "model": model,
        "reasoning_effort": effort,
        "api_key_masked": _mask_key(key),
        "has_key": bool(key),
    }


@app.post("/api/llm/config")
async def save_llm_config(body: dict):
    """保存接入配置（api_key 留空 = 保留原值），即时生效。"""
    updates: dict[str, str] = {}
    for k in ("base_url", "model"):
        if k in body:
            v = str(body[k]).strip()
            if not v:
                raise HTTPException(status_code=400, detail=f"{k} 不能为空")
            updates[k] = v
    if "reasoning_effort" in body:
        v = str(body["reasoning_effort"]).strip().lower()
        if v and v not in ("low", "high", "max"):
            raise HTTPException(status_code=400, detail="reasoning_effort 仅支持 low/high/max")
        updates["reasoning_effort"] = v
    api_key = str(body.get("api_key", "")).strip()
    if api_key:
        updates["api_key"] = api_key

    for k, v in updates.items():
        storage.set_setting(f"llm_{k}", v)
    llm.set_runtime_config(storage.get_llm_overrides())
    logger.info("LLM 配置已更新: %s（即时生效）",
                {k: (_mask_key(v) if k == "api_key" else v) for k, v in updates.items()})
    return {"saved": list(updates.keys())}


@app.post("/api/llm/test")
async def test_llm(body: dict):
    """连通性测试：用表单当前值（缺省回退当前生效值）发起最小真实调用。"""
    o = llm.runtime_overrides()
    base_url = str(body.get("base_url") or "").strip() or o.get("base_url") or settings.base_url
    api_key = str(body.get("api_key") or "").strip() or o.get("api_key") or settings.api_key
    model = str(body.get("model") or "").strip() or o.get("model") or settings.model
    effort = str(body.get("reasoning_effort") or "").strip() or o.get("reasoning_effort") or settings.reasoning_effort
    return await llm.test_connection(base_url, api_key, model, effort)


# 挂载前端静态资源（放在最后，避免覆盖 API 路由）
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def _port_free(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _pick_port(start: int = 8000, attempts: int = 50) -> int:
    """从 start 起找到第一个可用端口（被占用则顺延）。"""
    for port in range(start, start + attempts):
        if _port_free(port):
            return port
    raise RuntimeError(f"{start}~{start + attempts - 1} 端口均被占用，请手动释放后重试")


def _pid_file() -> Path:
    return settings.data_dir / "server.pid"


def _is_python_process(pid: int) -> bool:
    """校验 pid 是否属于 python 进程，防止 PID 被系统复用后误杀无关程序。"""
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        return "python" in r.stdout.lower()
    except Exception:  # noqa: BLE001
        logger.exception("进程名校验失败 (pid=%s)", pid)
        return False


def _shutdown_previous_instance() -> None:
    """单实例保护：发现旧实例（PID 锁文件）则先关闭，避免两个实例写同一 SQLite。

    仅当锁文件中的 pid 属于 python 进程时才终止；否则视为陈旧文件直接清理。
    """
    pf = _pid_file()
    if not pf.exists():
        return
    try:
        old_pid = int(pf.read_text(encoding="utf-8").strip())
    except Exception:  # noqa: BLE001
        logger.exception("PID 锁文件损坏，已重置: %s", pf)
        pf.unlink(missing_ok=True)
        return
    if old_pid == os.getpid():
        return

    killed = False
    if sys.platform == "win32":
        if _is_python_process(old_pid):
            r = subprocess.run(["taskkill", "/PID", str(old_pid), "/F"],
                               capture_output=True, text=True, timeout=10)
            killed = r.returncode == 0
            if not killed:
                logger.warning("旧实例关闭失败 (pid=%s): %s", old_pid, r.stderr.strip())
    else:
        try:
            os.kill(old_pid, signal.SIGTERM)
            killed = True
        except OSError:
            pass

    if killed:
        logger.warning("已自动关闭旧实例 (pid=%s)", old_pid)
        print(f"  已自动关闭旧实例 (pid={old_pid})")
    else:
        logger.info("未发现运行中的旧实例 (pid=%s)，清理锁文件", old_pid)
    pf.unlink(missing_ok=True)


def run() -> None:
    """本地启动：uv run hr-cv（自动关闭旧实例；端口被占自动顺延，如 8001、8002…）"""
    import uvicorn

    _shutdown_previous_instance()

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _pid_file().write_text(str(os.getpid()), encoding="utf-8")

    # 旧实例刚被强制结束时端口可能短暂滞留，稍候重试
    port = _pick_port(8000) if _port_free(8000) else _pick_port(8001)
    if port != 8000:
        logger.warning("端口 8000 被占用，自动改用 %d", port)
    print(f"\n  简历筛选助手运行中: http://127.0.0.1:{port}   （Ctrl+C 停止）\n")
    uvicorn.run("hr_cv.main:app", host="127.0.0.1", port=port)
