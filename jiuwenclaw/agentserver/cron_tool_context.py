"""Per-task context for cron tool routing (channel / session / request metadata)."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from jiuwenclaw.gateway.cron.models import CronTargetChannel

CRON_TOOL_CHANNEL_ID: ContextVar[str] = ContextVar(
    "cron_tool_channel_id",
    default=CronTargetChannel.WEB.value,
)
CRON_TOOL_SESSION_ID: ContextVar[str | None] = ContextVar(
    "cron_tool_session_id",
    default=None,
)
CRON_TOOL_METADATA: ContextVar[dict[str, Any] | None] = ContextVar(
    "cron_tool_metadata",
    default=None,
)
CRON_TOOL_MODE: ContextVar[str | None] = ContextVar(
    "cron_tool_mode",
    default=None,
)


def get_cron_tool_channel_id() -> str:
    return CRON_TOOL_CHANNEL_ID.get()


def get_cron_tool_session_id() -> str | None:
    return CRON_TOOL_SESSION_ID.get()


def get_cron_tool_metadata() -> dict[str, Any] | None:
    return CRON_TOOL_METADATA.get()


def get_cron_tool_mode() -> str | None:
    return CRON_TOOL_MODE.get()
