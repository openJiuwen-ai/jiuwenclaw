# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Cancel orphaned active todos before a fresh (non-resume) user turn."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_RESUME_QUERY_RE = re.compile(
    r"^(继续|接着做|接着|resume|continue)([。.!！\s].*)?$",
    re.IGNORECASE,
)


def is_resume_user_query(query: str) -> bool:
    """Return True for short explicit resume phrases."""
    text = str(query or "").strip()
    if not text:
        return False
    return bool(_RESUME_QUERY_RE.match(text))


def should_cancel_stale_active_todos(request: Any, params: dict[str, Any]) -> bool:
    """Return True when this turn should clear prior active todos on disk.

    Skips heartbeat, supplement turns, structured replies, and explicit resume
    phrases. Aligns with product intent: a new user message replaces the prior
    task unless the user explicitly continues.
    """
    session_id = str(getattr(request, "session_id", "") or "")
    if session_id.startswith("heartbeat"):
        return False

    if params.get("is_supplement"):
        return False

    query = params.get("query")
    # Structured interactive replies should not wipe todos.
    type_name = type(query).__name__ if query is not None else ""
    if type_name == "InteractiveInput":
        return False

    if params.get("answers"):
        return False

    if is_resume_user_query(str(query or "")):
        return False

    return True


async def prepare_stale_todo_cleanup_for_request(
    adapter: Any,
    request: Any,
) -> bool:
    """Cancel active todos before a fresh user turn when applicable.

    Uses the adapter's ``_cancel_pending_todos`` (already avoids shared
    Runner.resource_mgr lookups) instead of enterprise TaskExecutionRail skip
    flags that do not exist on develop.
    """
    session_id = str(getattr(request, "session_id", "") or "").strip()
    if not session_id:
        return False

    params = request.params if isinstance(getattr(request, "params", None), dict) else None
    if params is None:
        return False

    if not should_cancel_stale_active_todos(request, params):
        return False

    cancel = getattr(adapter, "_cancel_pending_todos", None)
    if not callable(cancel):
        return False

    try:
        updated = await cancel(session_id)
        if updated is None:
            return False
        logger.info(
            "[JiuWenSwarmDeepAdapter] cancelled stale active todos for new turn "
            "session_id=%s request_id=%s",
            session_id,
            getattr(request, "request_id", ""),
        )
        return True
    except Exception as exc:
        logger.warning(
            "[JiuWenSwarmDeepAdapter] prepare_stale_todo_cleanup_for_request failed "
            "session_id=%s: %s",
            session_id,
            exc,
        )
        return False
