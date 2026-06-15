# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Rail that rewrites shell commands to use the isolated runtime venv."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.permissions.shell_tools import (
    SHELL_COMMAND_ARG_KEYS,
    extract_shell_command,
)
from jiuwenclaw.runtime.pip_env import rewrite_shell_command

logger = logging.getLogger(__name__)

_PIP_ISOLATION_TOOLS = frozenset(
    {"bash", "shell", "mcp_exec_command", "create_terminal", "exec_command"}
)


class PipIsolationRail(DeepAgentRail):
    """Rewrite pip/python shell commands before execution and permission checks."""

    priority: int = 91  # Before PermissionRail (90)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return

        tool_name = (ctx.inputs.tool_name or "").strip()
        if tool_name not in _PIP_ISOLATION_TOOLS:
            return

        tool_call = ctx.inputs.tool_call
        if tool_call is None:
            return

        args = _parse_tool_args(tool_call)
        command = extract_shell_command(args)
        if not command:
            return

        rewritten = rewrite_shell_command(command)

        updated = dict(args)
        for key in SHELL_COMMAND_ARG_KEYS:
            if key in updated and isinstance(updated[key], str):
                updated[key] = rewritten
                break
        else:
            updated["command"] = rewritten

        _write_tool_args(tool_call, updated)
        # ability_manager._railed_execute_single_tool_call restores tool_call.arguments
        # from ctx.inputs.tool_args before invoke; keep them in sync.
        ctx.inputs.tool_args = tool_call.arguments

        if rewritten != command:
            logger.info(
                "[PipIsolationRail] Rewrote shell command for tool=%s",
                tool_name,
            )


def _parse_tool_args(tool_call: Any) -> dict[str, Any]:
    args = getattr(tool_call, "arguments", None)
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(args, dict):
        return dict(args)
    return {}


def _write_tool_args(tool_call: Any, args: dict[str, Any]) -> None:
    if hasattr(tool_call, "arguments"):
        current = tool_call.arguments
        if isinstance(current, str):
            tool_call.arguments = json.dumps(args, ensure_ascii=False)
        else:
            tool_call.arguments = args
