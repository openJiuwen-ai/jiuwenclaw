# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime patches for OpenAIModelClient.

1. SSE-only 网关兼容：部分网关（如 celia-claw sse-api）即使在非流式调用下也只返回
   ``text/event-stream`` 文本，此时 openai SDK 交回给框架的 ``response`` 会是 ``str``
   而非 ``ChatCompletion``，导致 ``response.choices`` 抛出异常。补丁在收到 ``str``
   响应时，先把 SSE 文本组装成标准的 ``ChatCompletion`` 再交回原解析逻辑。

2. GLM XML 标签清洗：流式场景下 GLM 调用原生工具的 XML 标签（如 ``<arg_value>``、
   ``<arg_key>``、``<tool_call>``）未剥离干净，会污染后续 todo_list、LLM 上下文及
   前端。补丁在解析流式 chunk 和非流式响应时，对 tool_call.arguments 做标签清洗。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

logger = logging.getLogger("jiuwenswarm.llm_sse_patch")

_GLM_TOOL_XML_CLOSED_RE = re.compile(
    r"<(arg_value|arg_key|tool_call)[^>]*?>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_GLM_TOOL_XML_TRUNCATED_OPEN_RE = re.compile(
    r"^.*?<(?:arg_value|arg_key|tool_call)[^>]*?>",
    re.IGNORECASE | re.DOTALL,
)


def _sanitize_glm_tool_xml_tags(raw: str) -> str:
    """Strip GLM native XML tags and their inner content.

    Complete closed tags: remove entire segment including inner content.
    The closing tag name must match the opening tag name, so nested tags
    of different names are handled correctly without leaving stray
    closing tags behind.
    Truncated open tags: delete preceding text + the tag itself,
    preserve content after the tag.
      e.g. 'prefix<tool_call...>suffix' -> 'suffix'
    The ^-anchored regex is applied repeatedly until no truncated
    open tag remains, so multiple truncated tags are all removed.

    The early-exit guard checks for '<arg_' and '<tool_call' in
    lowercased input so that uppercase variants (e.g. <TOOL_CALL>)
    are also caught by the subsequent regex substitutions that use
    re.IGNORECASE.
    """
    if not raw:
        return raw
    lowered = raw.lower()
    if "<arg_" not in lowered and "<tool_call" not in lowered:
        return raw
    result = _GLM_TOOL_XML_CLOSED_RE.sub("", raw)
    prev = None
    while prev != result:
        prev = result
        result = _GLM_TOOL_XML_TRUNCATED_OPEN_RE.sub("", result)
    return result

_PATCH_APPLIED = False


def _parse_chunk(chunk_str: str) -> dict | None:
    """解析单个数据块 JSON。"""
    if not chunk_str or not chunk_str.startswith("data:"):
        return None
    try:
        return json.loads(chunk_str[5:].strip())
    except json.JSONDecodeError as e:
        logger.info("[ParserPatch] JSON 解析错误: %s", e)
        return None


def _extract_message_content(chunk: dict) -> tuple[str, str]:
    """从 chunk 中提取思考内容和输出内容。"""
    if not chunk or not chunk.get("choices"):
        return "", ""
    msg = chunk["choices"][0]["message"]
    return msg.get("reasoning_token_text", ""), msg.get("token_text", "")


def _build_tool_calls(msg: dict) -> list | None:
    """从消息中构建工具调用对象列表。"""
    if not msg.get("tool_calls"):
        return None
    from openai.types.chat import ChatCompletionMessageFunctionToolCall
    from openai.types.chat.chat_completion_message_function_tool_call import Function

    return [
        ChatCompletionMessageFunctionToolCall(
            id=tc["id"],
            type="function",
            function=Function(
                name=tc["function"]["name"],
                arguments=_sanitize_glm_tool_xml_tags(tc["function"]["arguments"]),
            ),
        )
        for tc in msg["tool_calls"]
    ]


def assemble_openai_response(response: str) -> Any:
    """将分块 SSE 数据组装成标准的 OpenAI ``ChatCompletion``。"""
    from openai.types.chat import ChatCompletion, ChatCompletionMessage
    from openai.types.chat.chat_completion import Choice
    from openai.types.completion_usage import CompletionUsage

    content = think_content = ""
    last_chunk = None
    cache_chunk = ""

    for line in response.split("\n"):
        if not line.strip():
            continue
        if line.startswith("id:"):
            if cache_chunk:
                chunk = _parse_chunk(cache_chunk)
                cache_chunk = ""
                if chunk:
                    think, out = _extract_message_content(chunk)
                    think_content += think
                    content += out
                    last_chunk = chunk
        elif line.startswith("data:"):
            cache_chunk = line

    # 处理最后一个 chunk
    if cache_chunk:
        chunk = _parse_chunk(cache_chunk)
        if chunk:
            think, out = _extract_message_content(chunk)
            think_content += think
            content += out
            last_chunk = chunk

    # 提取并构建工具调用对象
    formatted_tool_calls = None
    if last_chunk and last_chunk.get("choices"):
        msg = last_chunk["choices"][0]["message"]
        formatted_tool_calls = _build_tool_calls(msg)

    # 构建 message（扩展 reasoning_content 字段存储思考内容）
    message = ChatCompletionMessage(
        role="assistant",
        content=content or None,
        tool_calls=formatted_tool_calls,
    )
    if think_content:
        message.reasoning_content = think_content

    # 构建 usage
    usage = None
    if last_chunk and last_chunk.get("usage"):
        u = last_chunk["usage"]
        usage = CompletionUsage(
            prompt_tokens=u.get("prompt_tokens", 0),
            completion_tokens=u.get("completion_tokens", 0),
            total_tokens=u.get("total_tokens", 0),
        )

    # 构建 finish_reason：有工具调用时优先使用 "tool_calls"
    finish_reason = "stop"
    if formatted_tool_calls:
        finish_reason = "tool_calls"
    elif last_chunk and last_chunk.get("choices"):
        finish_reason = last_chunk["choices"][0].get("finish_reason", "stop")

    return ChatCompletion(
        id=last_chunk.get("id", "chatcmpl-default") if last_chunk else "chatcmpl-default",
        choices=[
            Choice(
                index=0,
                message=message,
                finish_reason=finish_reason,
            )
        ],
        created=int(time.time()),
        model="unknown",
        object="chat.completion",
        usage=usage,
    )


def apply_openai_sse_invoke_patch() -> None:
    """给 ``OpenAIModelClient`` 打补丁：

    1. SSE-only 网关兼容：非流式调用下返回 str 时，先组装成标准 ChatCompletion。
    2. GLM XML 标签清洗：流式 chunk 中 tool_call.arguments 的 XML 标签剥离。

    幂等：重复调用只生效一次。在服务启动早期调用即可覆盖 subagent / 心跳等
    所有走 ``invoke()`` / ``stream()`` 的 LLM 调用。
    """
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    try:
        from openjiuwen.core.foundation.llm.model_clients.openai_model_client import (
            OpenAIModelClient,
        )
    except Exception as exc:  # pragma: no cover - openjiuwen 不可用时静默跳过
        logger.warning("[llm_sse_patch] 未能导入 OpenAIModelClient，跳过补丁: %s", exc)
        return

    if getattr(OpenAIModelClient, "_sse_invoke_patch_applied", False):
        _PATCH_APPLIED = True
        return

    # --- Patch 1: _parse_response (SSE-only 网关兼容) ---
    _orig_parse_response = OpenAIModelClient._parse_response  # pylint: disable=protected-access

    async def _parse_response_with_sse_guard(
        self: Any,
        response: Any,
        parser: Optional[Any] = None,
    ):
        if isinstance(response, str):
            response = assemble_openai_response(response)
        return await _orig_parse_response(self, response, parser)

    OpenAIModelClient._parse_response = _parse_response_with_sse_guard  # pylint: disable=protected-access

    # --- Patch 2: _parse_stream_chunk (GLM XML 标签清洗) ---
    _orig_parse_stream_chunk = OpenAIModelClient._parse_stream_chunk  # pylint: disable=protected-access

    def _parse_stream_chunk_with_sanitize(self: Any, chunk: Any):
        result = _orig_parse_stream_chunk(self, chunk)
        if result is not None and getattr(result, "tool_calls", None):
            for tc in result.tool_calls:
                orig_args = getattr(tc, "arguments", None)
                if isinstance(orig_args, str):
                    sanitized = _sanitize_glm_tool_xml_tags(orig_args)
                    if sanitized != orig_args:
                        try:
                            tc.arguments = sanitized
                        except (AttributeError, TypeError):
                            pass
        return result

    OpenAIModelClient._parse_stream_chunk = _parse_stream_chunk_with_sanitize  # pylint: disable=protected-access

    OpenAIModelClient._sse_invoke_patch_applied = True  # pylint: disable=protected-access
    _PATCH_APPLIED = True
    logger.info("[llm_sse_patch] OpenAIModelClient SSE 兼容补丁 + GLM XML 标签清洗补丁已应用")
