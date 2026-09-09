"""LLM 对接：基于 OpenAI 兼容接口（AsyncOpenAI），对简历做批量候选匹配。

DeepSeek V4 系列默认开启思考模式（官方协议）：
- 思维链经 delta.reasoning_content 下发（与 content 同级）；
- 最终回答经 delta.content 下发。
stream_match 将两类增量分别产出为 ("reasoning", text) / ("delta", text)，
由上层转换为不同 SSE 事件；整流无 content 时产出 ("error", msg) 兜底。

注意：思考模式下 temperature/top_p 等参数不生效（官方文档），故不传。
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from hr_cv.config import settings

logger = logging.getLogger(__name__)

# 页面保存的运行时配置（优先于 .env）：base_url / api_key / model / reasoning_effort
_RUNTIME: dict[str, str] = {}


def set_runtime_config(overrides: dict[str, str]) -> None:
    """由 main.py 在启动与保存配置时调用。"""
    _RUNTIME.clear()
    _RUNTIME.update({k: v for k, v in overrides.items() if v})


def runtime_overrides() -> dict[str, str]:
    return dict(_RUNTIME)


def _resolve(key: str) -> str:
    """运行时配置优先，缺省回退 .env 默认。"""
    if _RUNTIME.get(key):
        return _RUNTIME[key]
    return {
        "base_url": settings.base_url,
        "api_key": settings.api_key,
        "model": settings.model,
    }.get(key, "")


def is_configured() -> bool:
    """LLM 接入是否已配置（api_key / base_url / model 三项齐全）。"""
    return bool(_resolve("api_key") and _resolve("base_url") and _resolve("model"))


def _resolve_effort() -> str:
    return _RUNTIME.get("reasoning_effort") or settings.reasoning_effort


def _make_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=_resolve("api_key") or "EMPTY",
        base_url=_resolve("base_url"),
    )

SYSTEM_PROMPT = """你是资深招聘助理。用户会提供一批简历（可能是文字或图片），并根据岗位要求在其中筛选符合条件的人。
请依据简历中的真实信息作答，不要编造。输出请使用干净易懂的 Markdown（标题、加粗、列表均可）。输出时请：
1. 先给出符合条件并有优势的候选人名单（按匹配度从高到低），每人一行，注明姓名、关键技能/经验摘要、以及匹配的理由；
2. 对明显不符合条件的关键要求项，简要说明缺失在哪；
3. 若没有任何候选人符合，请明确说明。

引用规则：每当提到某位候选人，请在其姓名后方用双方括号标注该简历的序号，
格式为 姓名[[序号]]，序号对应"待筛选简历"中的顺序（从 1 开始）。
示例：韩磊[[1]]、王小明[[4]]。同一人多次提到可只标注一次。
最后可用一个"参考资料"小节，列出被引用的序号对应的简历文件名。请用简体中文回答。"""

# 单次请求最多发送的页面图片总数（防止载荷过大导致连接失败）
MAX_TOTAL_PAGES = 20


def _content_for(resume: dict, page_budget: list[int]) -> list[dict]:
    """把一个简历转成消息 content 块：文本 + （可选）PDF 页面图片。

    page_budget 是单元素列表，作为可变的剩余图片配额（超出配额的页跳过）。
    """
    blocks: list[dict] = [
        {"type": "text", "text": f"\n——— 简历N（{resume['filename']}，来源 {resume['method']}）———"}
    ]
    if settings.pdf_vision and resume.get("pages"):
        for url in resume["pages"]:
            if page_budget[0] <= 0:
                break
            blocks.append({"type": "image_url", "image_url": {"url": url}})
            page_budget[0] -= 1
    blocks.append({"type": "text", "text": resume["content"]})
    return blocks


def _build_messages(prompt: str, resumes: list[dict]) -> list[dict[str, Any]]:
    """构造多模态消息。有图片时 content 为块列表，否则为纯文本字符串。"""
    has_images = bool(resumes) and any(
        settings.pdf_vision and r.get("pages") for r in resumes
    )

    preamble = f"岗位/筛选要求：{prompt}\n待筛选简历如下：\n"
    closing = "\n请基于以上简历内容，结合岗位要求，给出筛选结论。"

    if has_images:
        content: Any = [{"type": "text", "text": preamble}]
        budget = [MAX_TOTAL_PAGES]
        for i, r in enumerate(resumes, 1):
            for block in _content_for(r, budget):
                block = dict(block)
                if block["type"] == "text":
                    block["text"] = block["text"].replace("简历N", f"简历{i}")
                content.append(block)
        content.append({"type": "text", "text": closing})
    else:
        parts = [preamble]
        for i, r in enumerate(resumes, 1):
            parts.append(f"\n——— 简历{i}（{r['filename']}）———\n{r['content']}")
        parts.append(closing)
        content = "".join(parts)

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


async def stream_match(prompt: str, resumes: list[dict]) -> AsyncIterator[tuple[str, str]]:
    """异步流式筛选：产出 ("reasoning"|"delta"|"error", 文本) 元组。"""
    client = _make_client()
    effort = _resolve_effort()
    extra: dict[str, Any] = {"extra_body": {"reasoning_effort": effort}} if effort else {}
    stream = await client.chat.completions.create(
        model=_resolve("model"),
        messages=_build_messages(prompt, resumes),
        stream=True,
        **extra,
    )
    got_content = False
    finish_reason = None
    async for chunk in stream:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        if choice.finish_reason:
            finish_reason = choice.finish_reason
        delta = choice.delta
        reasoning = getattr(delta, "reasoning_content", None)
        if reasoning:
            yield "reasoning", reasoning
        content = getattr(delta, "content", None)
        if content:
            got_content = True
            yield "delta", content
    logger.info("LLM 流结束: finish_reason=%s, has_content=%s", finish_reason, got_content)
    if not got_content:
        yield "error", "模型仅返回思考过程、未输出最终回答，请重试或减少简历数量"


async def test_connection(base_url: str, api_key: str, model: str,
                          reasoning_effort: str = "") -> dict:
    """连通性测试：用给定参数发起最小真实 chat 调用。

    成功: {ok: True, model, reply, latency_ms}
    失败: {ok: False, error_type, status?, error} —— error 为 API 原始报错，不改写。
    """
    t0 = time.monotonic()
    client = AsyncOpenAI(api_key=api_key or "EMPTY", base_url=base_url)
    extra: dict[str, Any] = {"extra_body": {"reasoning_effort": reasoning_effort}} if reasoning_effort else {}
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=16,
            **extra,
        )
        ms = int((time.monotonic() - t0) * 1000)
        reply = ""
        if resp.choices:
            reply = (resp.choices[0].message.content or "").strip()
        logger.info("连通性测试成功: model=%s 延迟=%dms", model, ms)
        return {"ok": True, "model": model, "reply": reply[:100], "latency_ms": ms}
    except Exception as e:  # noqa: BLE001 - 原始错误透出给前端
        logger.exception("连通性测试失败: model=%s base_url=%s", model, base_url)
        out: dict[str, Any] = {"ok": False, "error_type": type(e).__name__, "error": str(e)}
        status = getattr(e, "status_code", None)
        if status is not None:
            out["status"] = status
        return out
