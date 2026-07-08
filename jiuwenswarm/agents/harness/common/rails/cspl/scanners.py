# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""CSPL payload builders for tool input/output (ported from xy_channel utils.ts)."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from jiuwenswarm.common.utils import logger

from jiuwenswarm.agents.harness.common.rails.cspl.constants import (
    FILTER_TEXT_REGEX,
    MAX_TEXT_LENGTH,
    MAX_TOTAL_LENGTH,
    MESSAGE_TOOLS,
    OUTPUT_SCAN_TOOLS,
    SECURITY_NOTICE,
    SHELL_TOOLS,
    TOOL_INPUT_DEFAULT,
    TOOL_NAME_ALIASES,
    WEB_FETCH_TOOLS,
)

_MAX_COLLECT_DEPTH = 100


def normalize_tool_name(tool_name: str) -> str:
    """Map OpenClaw/sandbox aliases to canonical tool names."""
    name = (tool_name or "").strip()
    return TOOL_NAME_ALIASES.get(name, name)


def _coerce_tool_args(tool_args: Any) -> dict[str, Any]:
    """OpenJiuwen often passes tool_call.arguments as a JSON string."""
    if tool_args is None:
        return {}
    if isinstance(tool_args, dict):
        return tool_args
    if isinstance(tool_args, str):
        text = tool_args.strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        return {"value": tool_args}
    return {"value": tool_args}


def filter_text(text: str) -> str:
    if not text:
        return ""
    return FILTER_TEXT_REGEX.sub("", text)


def validate_and_truncate_text(text: str, max_length: int) -> tuple[str, bool]:
    if len(text) <= max_length:
        return text, False
    half = max_length // 2
    return text[:half] + text[len(text) - half :], True


def adjust_content_length(data: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    adjusted = dict(data)
    body_str = json.dumps(adjusted, ensure_ascii=False)
    if len(body_str) <= MAX_TEXT_LENGTH:
        return adjusted

    last_field = ""
    for field_name in fields:
        last_field = field_name
        body_str = json.dumps(adjusted, ensure_ascii=False)
        over_size = len(body_str) - MAX_TEXT_LENGTH
        current = adjusted.get(field_name)
        if isinstance(current, str) and len(current) > over_size:
            adjusted[field_name] = current[: len(current) - over_size]
        else:
            adjusted[field_name] = ""
        body_str = json.dumps(adjusted, ensure_ascii=False)
        if len(body_str) <= MAX_TEXT_LENGTH:
            break

    if len(json.dumps(adjusted, ensure_ascii=False)) > MAX_TEXT_LENGTH:
        raise ValueError(f"Field {last_field} exceeds length limit, unable to send data.")
    return adjusted


def _content_hash(content: str) -> str:
    if not content:
        return ""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _size_kb(text: str) -> int:
    return max(1, math.ceil(len(text.encode("utf-8")) / 1024))


def build_tool_input_payload(tool_name: str, tool_args: Any) -> str | None:
    """Build TOOL_INPUT JSON string for CSPL, or None if nothing to scan."""
    tool_name = normalize_tool_name(tool_name)
    tool_args = _coerce_tool_args(tool_args)

    if tool_name in SHELL_TOOLS:
        command = tool_args.get("command") or tool_args.get("cmd") or ""
        if not command:
            return None
        command = str(command)
        data = {
            **TOOL_INPUT_DEFAULT,
            "tool": tool_name,
            "hash": _content_hash(command),
            "size": _size_kb(command),
            "source": command,
        }
        adjusted = adjust_content_length(data, ["source"])
        return json.dumps(adjusted, ensure_ascii=False)

    if tool_name in MESSAGE_TOOLS:
        message = (
            tool_args.get("message")
            or tool_args.get("content")
            or tool_args.get("text")
            or ""
        )
        if not message:
            return None
        message = str(message)
        data = {
            **TOOL_INPUT_DEFAULT,
            "tool": tool_name,
            "hash": _content_hash(message),
            "size": _size_kb(message),
            "content": message,
        }
        adjusted = adjust_content_length(data, ["content"])
        return json.dumps(adjusted, ensure_ascii=False)

    if not tool_args:
        return None

    params_json = json.dumps(tool_args, ensure_ascii=False, default=str)
    data = {
        **TOOL_INPUT_DEFAULT,
        "tool": tool_name,
        "hash": _content_hash(params_json),
        "size": _size_kb(params_json),
        "content": params_json,
    }
    adjusted = adjust_content_length(data, ["content"])
    return json.dumps(adjusted, ensure_ascii=False)


def _collect_texts(
    value: Any,
    texts: list[str],
    *,
    _depth: int = 0,
    _visited: set[int] | None = None,
) -> None:
    if _depth >= _MAX_COLLECT_DEPTH:
        logger.warning("[CsplScanners] _collect_texts max depth exceeded depth=%s", _depth)
        return
    if value is None:
        return
    if isinstance(value, str):
        if value.strip():
            texts.append(value)
        return
    if isinstance(value, dict):
        obj_id = id(value)
        if _visited is None:
            _visited = set()
        if obj_id in _visited:
            logger.warning("[CsplScanners] _collect_texts cyclic reference detected")
            return
        _visited.add(obj_id)
        for key in ("text", "content", "stdout", "stderr", "output"):
            if key in value:
                _collect_texts(value[key], texts, _depth=_depth + 1, _visited=_visited)
        if "details" in value and isinstance(value["details"], dict):
            _collect_texts(
                value["details"].get("text"),
                texts,
                _depth=_depth + 1,
                _visited=_visited,
            )
        if not texts:
            for item in value.values():
                _collect_texts(item, texts, _depth=_depth + 1, _visited=_visited)
        return
    if isinstance(value, list):
        obj_id = id(value)
        if _visited is None:
            _visited = set()
        if obj_id in _visited:
            logger.warning("[CsplScanners] _collect_texts cyclic reference detected")
            return
        _visited.add(obj_id)
        for item in value:
            _collect_texts(item, texts, _depth=_depth + 1, _visited=_visited)


def extract_tool_output_text(tool_name: str, tool_result: Any) -> str | None:
    """Extract scannable text from a tool result."""
    tool_name = normalize_tool_name(tool_name)
    if tool_name not in OUTPUT_SCAN_TOOLS:
        return None

    texts: list[str] = []
    _collect_texts(tool_result, texts)
    if not texts:
        return None

    joined = "; ".join(texts)
    if tool_name in WEB_FETCH_TOOLS:
        joined = joined.replace(SECURITY_NOTICE, "")

    if len(joined) > MAX_TOTAL_LENGTH:
        return None

    filtered = filter_text(joined)
    final_text, _ = validate_and_truncate_text(filtered, MAX_TEXT_LENGTH)
    return final_text or None


def build_tool_output_payload(tool_name: str, tool_result: Any) -> str | None:
    """Build TOOL_OUTPUT JSON string for CSPL."""
    tool_name = normalize_tool_name(tool_name)
    origin_text = extract_tool_output_text(tool_name, tool_result)
    if not origin_text:
        return None

    question = {
        "subSceneID": "TOOL_OUTPUT",
        "tool": tool_name,
        "output": [{"content": origin_text}],
    }
    post_text = json.dumps(question, ensure_ascii=False)
    if len(post_text) > MAX_TEXT_LENGTH:
        diff = len(post_text) - MAX_TEXT_LENGTH
        truncated, _ = validate_and_truncate_text(origin_text, MAX_TEXT_LENGTH - diff)
        question["output"][0]["content"] = truncated
        post_text = json.dumps(question, ensure_ascii=False)
    return post_text
