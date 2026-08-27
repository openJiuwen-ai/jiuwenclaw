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

SHELL_PERMISSION_TOOLS = frozenset(
    {"bash", "shell", "mcp_exec_command", "create_terminal", "exec_command"}
)


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
        self._preset_session_id = preset_session_id

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
        active_skill = get_session_active_skill(session_id)
        if not active_skill:
            return

        credentials = self._skill_envs.get(active_skill, {})
        if not credentials:
            return

        # BashTool (agent-core) and mcp_exec_command both honor tool_args["env"].
        self._inject_credentials(ctx.inputs, credentials)
        logger.debug(
            "[SkillCredentialInjectionRail] injected env keys=%s for skill=%s tool=%s",
            list(credentials.keys()),
            active_skill,
            tool_name,
        )

    def _resolve_session_id(self, ctx: AgentCallbackContext) -> str:
        return resolve_skill_session_id(ctx, self._preset_session_id)

    def _inject_credentials(self, inputs: ToolCallInputs, credentials: dict[str, str]) -> None:
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
