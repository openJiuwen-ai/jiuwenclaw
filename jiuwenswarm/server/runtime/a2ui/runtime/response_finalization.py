# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime helpers for validating and repairing assistant A2UI responses."""

from __future__ import annotations

import logging
from typing import Any, Callable

from jiuwenswarm.server.runtime.a2ui.runtime.finalizer import A2UIResponseFinalizer, RepairCall


logger = logging.getLogger(__name__)

# Type for a function that can retry a request without A2UI
RetryWithoutA2UI = Callable[[str], Any]


async def finalize_a2ui_assistant_content(
    content: str,
    *,
    user_query: Any,
    request_id: str,
    repair_call: RepairCall | None,
    a2ui_enabled: bool,
    retry_without_a2ui_call: RetryWithoutA2UI | None = None,
) -> str:
    """Validate and repair a complete assistant response when A2UI is enabled.
    
    Flow:
    1. Validate A2UI content
    2. If invalid, try to repair (up to 2 times)
    3. If repair fails, retry user request WITHOUT A2UI prompt
    4. Return repaired A2UI, plain retry result, or safe plain text
    """
    if not a2ui_enabled or not isinstance(content, str) or "<a2ui-json>" not in content:
        return content

    try:
        finalization = await A2UIResponseFinalizer().finalize_result(
            content,
            user_query=user_query,
            request_id=request_id,
            repair_call=repair_call,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("A2UI response finalization failed: request_id=%s error=%s", request_id, exc)
        return content

    finalized = finalization.content
    if finalization.status == "repair_failed":
        logger.info(
            "A2UI repair failed, retrying without A2UI: request_id=%s",
            request_id,
        )
        # Retry without A2UI prompt
        if retry_without_a2ui_call is not None:
            try:
                retry_response = retry_without_a2ui_call(str(user_query or ""))
                import inspect
                if inspect.isawaitable(retry_response):
                    retry_response = await retry_response
                retry_content = _coerce_model_message_content(retry_response)
                if retry_content:
                    logger.info(
                        "Retry without A2UI succeeded: request_id=%s",
                        request_id,
                    )
                    return retry_content
            except Exception as retry_exc:
                logger.exception(
                    "Retry without A2UI failed: request_id=%s error=%s",
                    request_id,
                    retry_exc,
                )
    
    if finalized != content:
        logger.info("A2UI response finalized: request_id=%s changed=true", request_id)
    return finalized


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


__all__ = ["finalize_a2ui_assistant_content"]
