"""Request-local channel identity shared by tools and runtime adapters."""

from __future__ import annotations

import contextvars


CURRENT_CHANNEL_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jiuwenswarm_current_channel_id",
    default="",
)
CURRENT_SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jiuwenswarm_current_session_id",
    default="",
)


__all__ = ["CURRENT_CHANNEL_ID", "CURRENT_SESSION_ID"]
