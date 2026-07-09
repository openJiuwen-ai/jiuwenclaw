"""Tool permission channel context.

The openjiuwen permission rail uses host callbacks that need to know which
channel is executing (web/acp/tui). We keep this as a ContextVar owned by
jiuwenswarm so request handlers can set/reset it without depending on the
legacy permissions implementation.
"""

from __future__ import annotations

from jiuwenswarm.agents.harness.common.channel_runtime_context import (
    CURRENT_CHANNEL_ID,
    CURRENT_SESSION_ID,
)

# 当前 asyncio Task 的 channel_id（供工具权限/宿主确认判断）；由接口层在 run_agent 前 set、结束后 reset。
TOOL_PERMISSION_CHANNEL_ID = CURRENT_CHANNEL_ID
TOOL_PERMISSION_SESSION_ID = CURRENT_SESSION_ID


__all__ = ["TOOL_PERMISSION_CHANNEL_ID", "TOOL_PERMISSION_SESSION_ID"]
