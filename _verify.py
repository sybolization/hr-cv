"""端到端回归（Task 5）：12 份混传 + reasoning 流 + 持久化 + 会话 API。

用法：uv run python _verify.py [BASE]   默认 BASE=http://127.0.0.1:8001
"""
import io
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"


def req_json(path, obj=None, method=None, timeout=60):
    data = json.dumps(obj).encode() if obj is not None else None
    r = urllib.request.Request(BASE + path, data=data,
                               headers={"Content-Type": "application/json"},
                               method=method)
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


def multipart_upload(files, sid):
    b = uuid.uuid4().hex
    body = io.BytesIO()
    body.write(f"--{b}\r\n".encode())
    body.write(f'Content-Disposition: form-data; name="session_id"\r\n\r\n{sid}\r\n'.encode())
    for p in files:
        body.write(f"--{b}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="files"; filename="{p.name}"\r\n'.encode())
        body.write(f"Content-Type: {mimetypes.guess_type(p.name)[0] or 'application/octet-stream'}\r\n\r\n".encode())
        body.write(p.read_bytes())
        body.write(b"\r\n")
    body.write(f"--{b}--\r\n".encode())
    r = urllib.request.Request(BASE + "/api/upload", data=body.getvalue(),
                               headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    return json.loads(urllib.request.urlopen(r, timeout=120).read())


files = sorted(Path("sample_resumes").glob("OCR_*.pdf")) + sorted(Path("sample_resumes").glob("*.md"))

# [0.1] 健康检查
h = req_json("/api/health")
ok0a = h.get("app") == "hr-cv" and h.get("ok") is True and bool(h.get("version"))
print(f"[0.1] /api/health: app={h.get('app')} version={h.get('version')} -> {'PASS' if ok0a else 'FAIL'}")

# [0.2] LLM 未配置守卫 + 自动配置（先建临时会话，守卫无论在会话校验之前还是之后都成立）
probe_sid = req_json("/api/sessions", method="POST")["id"]
cfg = req_json("/api/llm/config")
ok0b = None
if cfg.get("has_key"):
    print("[0.2] 实例已配置 LLM，跳过未配置守卫用例")
else:
    guard_ok, detail_txt = False, ""
    try:
        r = urllib.request.Request(
            BASE + "/api/chat",
            data=json.dumps({"session_id": probe_sid, "message": "预检测试"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(r, timeout=60).close()  # 未抛异常 = 拿到 200 流，守卫失效
    except urllib.error.HTTPError as e:
        detail_txt = str(json.loads(e.read().decode()).get("detail", ""))
        guard_ok = e.code == 400 and "LLM_NOT_CONFIGURED" in detail_txt
    print(f"[0.2] 未配置守卫: detail={detail_txt[:120]} -> {'PASS' if guard_ok else 'FAIL'}")
    env = {}
    if Path(".env").exists():
        for line in Path(".env").read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    api_key = env.get("OPENAI_API_KEY", "")
    base_url = env.get("OPENAI_BASE_URL") or "https://api.deepseek.com"
    model = env.get("OPENAI_MODEL") or "deepseek-v4-flash-vision-exp"
    if not guard_ok:
        ok0b = False
    elif api_key:
        req_json("/api/llm/config", {"base_url": base_url, "model": model, "api_key": api_key}, method="POST")
        ok0b = bool(req_json("/api/llm/config").get("has_key"))
        print(f"[0.2] 已从 .env 配置 LLM: base_url={base_url} model={model} has_key={ok0b}")
    else:
        ok0b = None
        print("[0.2] .env 无 OPENAI_API_KEY，跳过配置（后续 LLM 用例可能失败）")
req_json(f"/api/sessions/{probe_sid}", method="DELETE")

sid = req_json("/api/sessions", method="POST")["id"]

# [1] 上传 12 份
up = multipart_upload(files, sid)
ok1 = len(up["created"]) == 12 and up["total"] == 12
print(f"[1] 混传 12 份: created={len(up['created'])} total={up['total']} -> {'PASS' if ok1 else 'FAIL'}")

# [2] 混传对话：reasoning 流 + delta + 时序
req = urllib.request.Request(
    BASE + "/api/chat",
    data=json.dumps({"session_id": sid, "message": "找一个有 Python 后端经验的人"}).encode(),
    headers={"Content-Type": "application/json"},
)
t0 = time.time()
n_reason = n_delta = 0
first_reason_at = first_delta_at = None
t_first_parse = t_meta = None
last = t0
text = ""
for raw in urllib.request.urlopen(req, timeout=600):
    now = time.time()
    line = raw.decode().rstrip()
    if not line.startswith("data:"):
        continue
    d = line[5:].strip()
    if d == "[DONE]":
        break
    ev = json.loads(d)
    if ev["type"] == "parse":
        if t_first_parse is None:
            t_first_parse = now
        last = now
    elif ev["type"] == "meta":
        t_meta = now
    elif ev["type"] == "reasoning":
        if first_reason_at is None:
            first_reason_at = now - t0
            print(f"    首个 reasoning 事件: {first_reason_at:.2f}s")
        n_reason += 1
        last = now
    elif ev["type"] == "delta":
        if first_delta_at is None:
            first_delta_at = now - t0
            print(f"    首个 delta 事件: {first_delta_at:.2f}s（思考阶段 {first_delta_at:.0f}s）")
        n_delta += 1
        text += ev["text"]
        last = now
    elif ev["type"] == "error":
        print(f"    错误: {ev['text'][:200]}")
ok2 = n_reason > 0 and n_delta > 0
parse_phase = (t_meta - t_first_parse) if (t_first_parse is not None and t_meta) else None
parse_ok = parse_phase is not None and parse_phase <= 8
print(f"[2] 解析阶段耗时（首parse→meta）: {parse_phase:.1f}s（目标 ≤8s）-> {'PASS' if parse_ok else 'FAIL'}" if parse_phase else "[2] 无解析阶段")
print(f"[2] 混传对话: reasoning 事件={n_reason} delta 事件={n_delta} 回复={len(text)}字 -> {'PASS' if ok2 else 'FAIL'}")
print("    回复开头:", text[:120].replace("\n", " "))

# [2.5] F1 确认：pages 为 jpeg（压缩生效）
from hr_cv.parser import parse_file as _pf
_p = _pf("jpeg_check.pdf", open("sample_resumes/OCR_韩磊.pdf", "rb").read())
jpeg_ok = bool(_p.pages) and _p.pages[0].startswith("data:image/jpeg")
print(f"[2.5] 模型图片为 jpeg: {jpeg_ok}")

# [3] 持久化：列表 + 标题 + 消息
lst = req_json("/api/sessions")
me = next((s for s in lst if s["id"] == sid), None)
title_ok = me and me["message_count"] == 2 and me["title"] != "新会话"
detail = req_json(f"/api/sessions/{sid}")
msgs_ok = (len(detail["messages"]) == 2
           and detail["messages"][1]["role"] == "assistant"
           and bool(detail["messages"][1].get("reasoning"))
           and detail["messages"][1].get("reasoning_seconds"))
resumes_ok = len(detail["resumes"]) == 12 and all(r["method"] for r in detail["resumes"])
print(f"[3] 持久化: 标题='{me['title'] if me else '?'}' 消息数={me['message_count'] if me else '?'} -> {'PASS' if title_ok and msgs_ok and resumes_ok else 'FAIL'}")

# [4] 引用详情接口
refs = detail["messages"][1].get("refs", [])
ok4 = False
if refs:
    rid = refs[0]["id"]
    d = req_json(f"/api/resume/{rid}?session_id={sid}")
    ok4 = bool(d.get("content"))
print(f"[4] 引用详情接口: {'PASS' if ok4 else 'FAIL'}")

# [5] 新建会话隔离 + 删除
sid2 = req_json("/api/sessions", method="POST")["id"]
lst2 = req_json("/api/sessions")
me2 = next((s for s in lst2 if s["id"] == sid2), {})
iso_ok = me2.get("resume_count") == 0 and me2.get("message_count") == 0
req_json(f"/api/sessions/{sid2}", method="DELETE")
lst3 = req_json("/api/sessions")
del_ok = all(s["id"] != sid2 for s in lst3)
print(f"[5] 会话隔离/删除: {'PASS' if iso_ok and del_ok else 'FAIL'}")

# 清理演示会话（保留磁盘干净）
req_json("/api/clear", {"session_id": sid}, method=None)
req_json(f"/api/sessions/{sid}", method="DELETE")
print("[清理] 演示会话已删除")

allpass = ok0a and (ok0b is not False) and ok1 and ok2 and parse_ok and jpeg_ok and title_ok and msgs_ok and resumes_ok and ok4 and iso_ok and del_ok
print("\n=== 全部通过 ===" if allpass else "\n=== 存在失败项 ===")