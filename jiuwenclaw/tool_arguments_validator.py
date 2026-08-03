# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

ToolArgumentsKind = Literal[
    "valid_object",
    "empty_object",
    "truncated",
    "invalid_json",
    "not_object",
    "not_string_or_dict",
]

_RECOVERY_HINT = "split_large_tool_arguments"


@dataclass(frozen=True)
class ToolArgumentsValidation:
    ok: bool
    normalized: str
    reason: str
    kind: ToolArgumentsKind
    finish_reason: str | None
    length: int


def tool_arguments_failure_payload(
    *,
    tool_name: str,
    validation: ToolArgumentsValidation,
) -> dict[str, Any]:
    if validation.kind == "truncated":
        recovery_hint = _RECOVERY_HINT
    elif validation.kind == "invalid_json":
        recovery_hint = "fix_json_syntax"
    else:
        recovery_hint = "check_arguments_format"
    return {
        "success": False,
        "skipped": True,
        "reason": validation.reason,
        "kind": validation.kind,
        "tool_name": tool_name,
        "recovery_hint": recovery_hint,
    }


def tool_arguments_failure_message(
    *,
    tool_name: str,
    validation: ToolArgumentsValidation,
) -> str:
    prefix = f"工具 {tool_name or 'unknown'} 的调用参数 JSON {validation.reason}，已跳过真实工具执行。"
    if validation.kind == "truncated":
        return (
            prefix
            + "参数疑似被截断（可能因内容过长被模型输出中断）。"
            "不要原样重试本次超大工具调用；请将一次性的大工具执行拆分成多次、多段执行，"
            "例如分批读取、分段写入，或按文件、章节、范围拆分，降低单次 tool arguments 长度。"
        )
    if validation.kind == "invalid_json":
        return (
            prefix
            + "参数不是合法 JSON（语法错误，如缺少引号、多余逗号、非法字符等）。"
            "请检查并确保：所有字符串值用双引号包裹（包括键名和值），"
            "不要在 JSON 中使用裸文本或单引号，不要包含未转义的换行符。"
            "修正 JSON 格式后重试，无需拆分参数。"
        )
    if validation.kind == "not_object":
        return (
            prefix
            + "参数解析结果不是 JSON object（例如是数组或字符串）。"
            "请确保 tool arguments 是一个 {...} 格式的 JSON 对象。"
        )
    return (
        prefix
        + "请检查参数格式并重试。"
    )


def validate_tool_arguments(
    arguments: Any,
    *,
    finish_reason: str | None = None,
) -> ToolArgumentsValidation:
    normalized_finish_reason = str(finish_reason) if finish_reason is not None else None
    if isinstance(arguments, dict):
        normalized = json.dumps(arguments, ensure_ascii=False)
        return ToolArgumentsValidation(
            ok=True,
            normalized=normalized,
            reason="是合法 JSON object",
            kind="empty_object" if not arguments else "valid_object",
            finish_reason=normalized_finish_reason,
            length=len(normalized),
        )

    if not isinstance(arguments, str):
        return ToolArgumentsValidation(
            ok=False,
            normalized="{}",
            reason=f"不是字符串或 dict，而是 {type(arguments).__name__}",
            kind="not_string_or_dict",
            finish_reason=normalized_finish_reason,
            length=0,
        )

    text = arguments.strip()
    if not text:
        return ToolArgumentsValidation(
            ok=False,
            normalized="{}",
            reason="为空字符串，不是可执行的 JSON object",
            kind="invalid_json",
            finish_reason=normalized_finish_reason,
            length=len(arguments),
        )

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        kind: ToolArgumentsKind = (
            "truncated"
            if _looks_truncated(text, exc, normalized_finish_reason)
            else "invalid_json"
        )
        reason = "疑似被截断或不完整" if kind == "truncated" else f"非法：{exc.msg}"
        return ToolArgumentsValidation(
            ok=False,
            normalized="{}",
            reason=reason,
            kind=kind,
            finish_reason=normalized_finish_reason,
            length=len(arguments),
        )

    if not isinstance(parsed, dict):
        return ToolArgumentsValidation(
            ok=False,
            normalized="{}",
            reason=f"解析结果不是 JSON object，而是 {type(parsed).__name__}",
            kind="not_object",
            finish_reason=normalized_finish_reason,
            length=len(arguments),
        )

    normalized = json.dumps(parsed, ensure_ascii=False)
    return ToolArgumentsValidation(
        ok=True,
        normalized=normalized,
        reason="是合法 JSON object",
        kind="empty_object" if not parsed else "valid_object",
        finish_reason=normalized_finish_reason,
        length=len(arguments),
    )


def _looks_truncated(
    text: str,
    error: json.JSONDecodeError,
    finish_reason: str | None,
) -> bool:
    if finish_reason == "length":
        return True
    message = error.msg.lower()
    if "unterminated string" in message:
        return _has_unclosed_json_structure(text)
    near_end = error.pos >= max(len(text) - 2, 0)
    if not near_end:
        return False
    if _has_unclosed_json_structure(text):
        return True
    truncated_error_markers = (
        "expecting value",
        "expecting property name",
        "expecting ',' delimiter",
        "expecting ':' delimiter",
    )
    for marker in truncated_error_markers:
        if marker in message:
            return True
    return False


def _has_unclosed_json_structure(text: str) -> bool:
    stack: list[str] = []
    in_string = False
    escape = False

    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append(char)
        elif char == "]":
            if stack and stack[-1] == "[":
                stack.pop()
        elif char == "}":
            if stack and stack[-1] == "{":
                stack.pop()

    return in_string or escape or bool(stack)
