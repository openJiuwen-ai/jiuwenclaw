# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Inject per-skill credentials into shell tool calls via tool_args["env"]."""

from __future__ import annotations

import json
import logging
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.agents.harness.common.rails.skill_active_state import (
    get_session_active_skill,
    resolve_skill_session_id,
)

logger = logging.getLogger(__name__)


def _nonempty_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


SHELL_PERMISSION_TOOLS = frozenset(
    {"bash", "shell", "mcp_exec_command", "create_terminal", "exec_command"}
)
_DEFAULT_SHELL_TYPES = frozenset({"auto", "cmd", "bash", "sh"})


def _should_strip_powershell_amp_prefix(
    tool_name: str, command: Any, shell_type: Any
) -> bool:
    """True when bash command uses PowerShell '&' prefix under a non-PowerShell shell."""
    if tool_name != "bash":
        return False
    if not isinstance(command, str) or not command.lstrip().startswith("& "):
        return False
    return shell_type in _DEFAULT_SHELL_TYPES


def coalesce_skill_envs(
    incoming: dict[str, dict[str, str]] | None,
    current: dict[str, dict[str, str]] | None,
) -> dict[str, dict[str, str]]:
    """Prefer incoming skill envs; keep current when incoming is empty."""
    incoming_map = incoming if isinstance(incoming, dict) else {}
    current_map = current if isinstance(current, dict) else {}
    if incoming_map:
        return incoming_map
    return current_map or incoming_map


def coalesce_config_skill_envs(config: Any, previous: Any) -> Any:
    """Keep previous react.skill_envs when config only has the YAML placeholder."""
    if not isinstance(config, dict):
        return config
    react = config.get("react")
    incoming = react.get("skill_envs") if isinstance(react, dict) else None
    previous_envs = None
    if isinstance(previous, dict):
        prev_react = previous.get("react")
        if isinstance(prev_react, dict):
            previous_envs = prev_react.get("skill_envs")
    resolved = coalesce_skill_envs(
        incoming if isinstance(incoming, dict) else None,
        previous_envs if isinstance(previous_envs, dict) else None,
    )
    if resolved == incoming or (not resolved and not incoming):
        return config
    merged = dict(config)
    merged_react = dict(react) if isinstance(react, dict) else {}
    merged_react["skill_envs"] = resolved
    merged["react"] = merged_react
    return merged


class SkillCredentialInjectionRail(DeepAgentRail):
    """Inject per-skill credentials into shell tool calls."""

    priority = 5

    def __init__(
        self,
        skill_envs: dict[str, dict[str, str]] | None = None,
        preset_session_id: str | None = None,
    ) -> None:
        super().__init__()
        self._skill_envs: dict[str, dict[str, str]] = skill_envs or {}
        self._preset_session_id = _nonempty_str(preset_session_id)

    def update_skill_envs(self, new_skill_envs: dict[str, dict[str, str]]) -> None:
        self._skill_envs = new_skill_envs or {}
        logger.info(
            "[SkillCredentialInjectionRail] skill_envs updated: skills=[%s]",
            ", ".join(self._skill_envs.keys()),
        )

    def get_skill_envs(self) -> dict[str, dict[str, str]]:
        return self._skill_envs

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return

        tool_name: str = ctx.inputs.tool_name
        if tool_name not in SHELL_PERMISSION_TOOLS:
            return

        session_id = self._resolve_session_id(ctx)
        # Read-only lookup: do not adopt default orphans here (cross-session
        # credential leak). Migration runs on HITL preserve before_invoke.
        active_skill = get_session_active_skill(session_id)
        if not active_skill:
            return

        credentials = self._skill_envs.get(active_skill, {})
        if not credentials:
            return

        # 注入凭据到 tool_args["env"]，BashTool 会转发给子进程。
        # 同时处理 PowerShell '&' 语法（去前缀保持 auto shell）。
        self._inject_credentials(ctx.inputs, credentials, tool_name)
        logger.debug(
            "[SkillCredentialInjectionRail] injected env keys=%s for skill=%s tool=%s",
            list(credentials.keys()),
            active_skill,
            tool_name,
        )

    def _resolve_session_id(self, ctx: AgentCallbackContext) -> str:
        return resolve_skill_session_id(ctx, self._preset_session_id)

    def _inject_credentials(
        self, inputs: ToolCallInputs, credentials: dict[str, str], tool_name: str
    ) -> None:
        tool_args: Any = inputs.tool_args
        if tool_args is None:
            return

        if isinstance(tool_args, str):
            try:
                parsed = json.loads(tool_args)
            except (json.JSONDecodeError, TypeError):
                return
            if not isinstance(parsed, dict):
                return
            tool_args = parsed
            inputs.tool_args = tool_args

        if isinstance(tool_args, dict):
            command = tool_args.get("command", "")

            # 当 bash 工具的命令以 PowerShell 调用操作符 '&' 开头时，
            # 去掉 '&' 前缀让命令在默认 shell(auto=Git Bash/cmd)下直接执行，
            # 避免 cmd 报 "& was unexpected" 及 Git Bash 的后台执行语义。
            # 若 LLM 显式指定了 powershell 则保留原样（尊重其意图）。
            shell_type = tool_args.get("shell_type", "auto")
            if _should_strip_powershell_amp_prefix(tool_name, command, shell_type):
                tool_args["command"] = command.lstrip()[2:].lstrip()

            env = tool_args.get("env")
            if not isinstance(env, dict):
                env = {}
            for key, value in credentials.items():
                if key not in env and value not in (None, ""):
                    env[key] = value
            tool_args["env"] = env


__all__ = [
    "SkillCredentialInjectionRail",
    "SHELL_PERMISSION_TOOLS",
    "coalesce_config_skill_envs",
    "coalesce_skill_envs",
]
