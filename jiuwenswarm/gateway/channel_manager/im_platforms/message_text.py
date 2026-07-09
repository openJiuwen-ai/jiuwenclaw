"""Shared outbound text filtering for third-party IM channels."""

from __future__ import annotations

import json
import re
from typing import Any

from jiuwenswarm.common.schema.message import EventType, Message

_THINK_BLOCK_RE = re.compile(r"<think\b[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<think\b[^>]*>.*", re.IGNORECASE | re.DOTALL)

_INTERMEDIATE_EVENTS = {
    "chat.delta",
    "chat.reasoning",
    "chat.tool_call",
    "chat.tool_update",
    "chat.tool_result",
    "todo.updated",
    "chat.processing_status",
    "chat.usage_metadata",
    "chat.usage_summary",
    "context.usage",
    "chat.evolution_status",
    "chat.subtask_update",
}

_TEXT_KEYS = (
    "output",
    "content",
    "text",
    "message",
    "summary",
    "result",
    "error",
)


def outbound_event_name(msg: Message) -> str:
    payload = msg.payload if isinstance(msg.payload, dict) else {}
    return str(getattr(msg.event_type, "value", None) or payload.get("event_type") or "").strip()


def strip_think_tags(text: str) -> str:
    cleaned = _THINK_BLOCK_RE.sub("", str(text or ""))
    cleaned = _THINK_OPEN_RE.sub("", cleaned)
    return cleaned.strip()


def is_thinking_only_content(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return True
    return not strip_think_tags(raw)


def is_reasoning_message(msg: Message) -> bool:
    if msg.event_type == EventType.CHAT_REASONING:
        return True
    payload = msg.payload if isinstance(msg.payload, dict) else {}
    return str(payload.get("source_chunk_type") or "").strip() == "llm_reasoning"


def should_skip_intermediate_message(msg: Message) -> bool:
    return outbound_event_name(msg) in _INTERMEDIATE_EVENTS or is_reasoning_message(msg)


def get_outbound_artifacts(msg: Message, kind: str | None = None) -> list[dict[str, Any]]:
    metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
    delivery = metadata.get("outbound_delivery")
    if not isinstance(delivery, dict):
        return []
    artifacts = delivery.get("artifacts")
    if not isinstance(artifacts, list):
        return []
    items = [item for item in artifacts if isinstance(item, dict)]
    if kind is not None:
        items = [item for item in items if item.get("kind") == kind]
    return items


def extract_human_text(
    msg: Message,
    *,
    interrupt_fallback: str = "正在继续处理，请稍后。",
    error_prefix: str = "⚠️ ",
) -> str:
    """Extract text suitable for a human IM conversation; never stringify raw dicts."""
    if is_reasoning_message(msg):
        return ""

    event_name = outbound_event_name(msg)
    payload = msg.payload if isinstance(msg.payload, dict) else {}
    params = msg.params if isinstance(msg.params, dict) else {}

    if event_name == "chat.error":
        err_text = _extract_preferred_text(payload.get("error"))
        return f"{error_prefix}{err_text or '处理出错'}".strip()

    if event_name == "heartbeat.relay":
        return _extract_preferred_text(payload.get("heartbeat"))

    if event_name in _INTERMEDIATE_EVENTS:
        return ""

    candidates: list[Any] = [
        params.get("content"),
        payload.get("content"),
        payload.get("output"),
        payload.get("message"),
        payload.get("text"),
        payload.get("error"),
        payload.get("result"),
    ]
    for candidate in candidates:
        text = _extract_preferred_text(candidate, interrupt_fallback=interrupt_fallback)
        if text:
            return text
    return ""


def _extract_preferred_text(value: Any, *, interrupt_fallback: str = "") -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        text = strip_think_tags(value)
        if not text or is_thinking_only_content(text):
            return ""
        parsed = _parse_structured_text(text)
        if parsed is not None:
            return _extract_preferred_text(parsed, interrupt_fallback=interrupt_fallback)
        if _looks_like_tool_or_permission_noise(text):
            return interrupt_fallback
        return text

    if isinstance(value, dict):
        result_type = str(value.get("result_type") or "").strip().lower()
        if result_type == "interrupt":
            return interrupt_fallback
        event_name = str(value.get("event_type") or "").strip()
        if event_name in _INTERMEDIATE_EVENTS:
            return ""
        for key in _TEXT_KEYS:
            if key in value:
                text = _extract_preferred_text(
                    value.get(key),
                    interrupt_fallback=interrupt_fallback,
                )
                if text:
                    return text
        return ""

    if isinstance(value, list):
        parts = [
            _extract_preferred_text(item, interrupt_fallback=interrupt_fallback)
            for item in value
        ]
        return "\n".join(part for part in parts if part).strip()

    return strip_think_tags(str(value)).strip()


def _parse_structured_text(text: str) -> Any | None:
    stripped = text.strip()
    if not (
        (stripped.startswith("{") and stripped.endswith("}"))
        or (stripped.startswith("[") and stripped.endswith("]"))
    ):
        return None
    try:
        return json.loads(stripped)
    except Exception:
        return None


def _looks_like_tool_or_permission_noise(text: str) -> bool:
    markers = (
        "result_type",
        "tool_call_id",
        "tool_calls",
        "tool_name",
        "工具授权",
        "需要授权",
        "request_permission",
        "payload_schema",
    )
    return any(marker in text for marker in markers)
