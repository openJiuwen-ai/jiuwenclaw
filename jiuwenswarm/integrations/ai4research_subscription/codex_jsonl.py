"""Strict parser for the pinned Codex CLI JSONL surface."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from .constants import MAX_JSONL_LINE_BYTES
from .contracts import ProviderTurnResult, ProviderUsage, parse_final_response
from .errors import CodexProviderError


_ALLOWED_EVENT_TYPES = {
    "thread.started",
    "turn.started",
    "item.started",
    "item.completed",
    "turn.completed",
}
_ALLOWED_ITEM_TYPES = {"reasoning", "agent_message"}


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CodexProviderError("invalid_output", "Codex returned invalid usage metadata.")
    return value


def parse_codex_jsonl(data: bytes, *, allowed_tool_names: set[str]) -> ProviderTurnResult:
    if not data:
        raise CodexProviderError("invalid_output", "Codex returned no output.")
    counts: dict[str, int] = {}
    final_texts: list[str] = []
    usage: ProviderUsage | None = None
    for raw_line in data.splitlines():
        if not raw_line:
            continue
        if len(raw_line) > MAX_JSONL_LINE_BYTES:
            raise CodexProviderError("output_too_large", "Codex returned an oversized output event.")
        try:
            event = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CodexProviderError("invalid_output", "Codex returned malformed JSONL output.") from exc
        if not isinstance(event, dict):
            raise CodexProviderError("invalid_output", "Codex returned an invalid output event.")
        event_type = event.get("type")
        if event_type not in _ALLOWED_EVENT_TYPES:
            raise CodexProviderError("forbidden_provider_action", "Codex attempted an unsupported action.")
        counts[event_type] = counts.get(event_type, 0) + 1
        if event_type in {"item.started", "item.completed"}:
            item = event.get("item")
            item_type = item.get("type") if isinstance(item, dict) else None
            if item_type not in _ALLOWED_ITEM_TYPES:
                raise CodexProviderError("forbidden_provider_action", "Codex attempted to use an internal tool.")
            if event_type == "item.completed" and item_type == "agent_message":
                text = item.get("text")
                if not isinstance(text, str):
                    raise CodexProviderError("invalid_output", "Codex returned an invalid final message.")
                final_texts.append(text)
        if event_type == "turn.completed":
            raw_usage = event.get("usage")
            if not isinstance(raw_usage, dict):
                raise CodexProviderError("invalid_output", "Codex omitted usage metadata.")
            usage = ProviderUsage(
                input_tokens=_nonnegative_int(raw_usage.get("input_tokens", 0)),
                cached_input_tokens=_nonnegative_int(raw_usage.get("cached_input_tokens", 0)),
                output_tokens=_nonnegative_int(raw_usage.get("output_tokens", 0)),
            )
    if counts.get("thread.started") != 1 or counts.get("turn.started") != 1 or counts.get("turn.completed") != 1:
        raise CodexProviderError("invalid_output", "Codex returned an incomplete turn lifecycle.")
    if len(final_texts) != 1 or usage is None:
        raise CodexProviderError("invalid_output", "Codex did not return exactly one final response.")
    try:
        final_payload = json.loads(final_texts[0])
    except json.JSONDecodeError as exc:
        raise CodexProviderError("invalid_output", "Codex returned malformed final JSON.") from exc
    result = parse_final_response(final_payload, allowed_tool_names=allowed_tool_names)
    return replace(result, usage=usage)
