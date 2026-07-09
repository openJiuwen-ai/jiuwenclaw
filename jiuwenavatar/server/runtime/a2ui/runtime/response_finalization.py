# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime helpers for validating and repairing assistant A2UI responses."""

from __future__ import annotations

import logging
from typing import Any

from jiuwenavatar.server.runtime.a2ui.runtime.finalizer import A2UIResponseFinalizer, RepairCall


logger = logging.getLogger(__name__)


async def finalize_a2ui_assistant_content(
    content: str,
    *,
    user_query: Any,
    request_id: str,
    repair_call: RepairCall | None,
    a2ui_enabled: bool,
) -> str:
    """Validate and repair a complete assistant response when A2UI is enabled."""
    if not a2ui_enabled or not isinstance(content, str) or "<a2ui-json>" not in content:
        return content

    try:
        finalized = await A2UIResponseFinalizer().finalize(
            content,
            user_query=user_query,
            request_id=request_id,
            repair_call=repair_call,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("A2UI response finalization failed: request_id=%s error=%s", request_id, exc)
        return content

    if finalized != content:
        logger.info("A2UI response finalized: request_id=%s changed=true", request_id)
    return finalized


__all__ = ["finalize_a2ui_assistant_content"]
