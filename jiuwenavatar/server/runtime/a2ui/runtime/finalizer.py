# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Final validation and repair for model-emitted A2UI responses."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from jiuwenavatar.server.runtime.a2ui.protocol import A2UI_OPEN_TAG, get_protocol_spec


RepairCall = Callable[[str], Any]


def _coerce_model_message_content(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        value = message.get("content") or message.get("output") or ""
        return value if isinstance(value, str) else str(value)
    value = getattr(message, "content", None)
    if isinstance(value, str):
        return value
    value = getattr(message, "output", None)
    if isinstance(value, str):
        return value
    return str(message) if message is not None else ""


def _a2ui_failure_text(content: str, validation_error: str) -> str:
    spec = get_protocol_spec()
    readable = spec.format_for_text_channel(content)
    if readable:
        return (
            "A2UI 界面生成失败，已退回纯文本结果。\n\n"
            f"{readable}"
        )
    return (
        "A2UI 界面生成失败，模型连续返回了不符合 A2UI 0.8 schema "
        f"的内容。最后一次校验错误：{validation_error}"
    )


class A2UIResponseFinalizer:
    """Validate, repair, or safely degrade a complete assistant response."""

    async def finalize(
            self,
            content: str,
            *,
            user_query: Any,
            request_id: str,
            repair_call: RepairCall | None,
            max_repair_attempts: int = 2,
    ) -> str:
        _ = request_id
        if A2UI_OPEN_TAG not in (content or ""):
            return content

        spec = get_protocol_spec()
        validation = spec.validate_response(content)
        if validation.valid:
            return content

        repaired_content = content
        last_error = validation.error
        for _attempt in range(1, max_repair_attempts + 1):
            if repair_call is None:
                break
            prompt = spec.build_repair_prompt(
                invalid_content=repaired_content,
                validation_error=last_error,
                user_query=str(user_query or ""),
            )
            response = repair_call(prompt)
            if inspect.isawaitable(response):
                response = await response
            repaired_content = _coerce_model_message_content(response)
            validation = spec.validate_response(repaired_content)
            if validation.valid:
                return repaired_content
            last_error = validation.error

        return _a2ui_failure_text(repaired_content, last_error)


__all__ = ["A2UIResponseFinalizer"]
