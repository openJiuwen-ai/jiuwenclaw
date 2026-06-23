# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SkillCredentialInjectionRail — inject per-skill env vars into shell tool calls.

When a skill is active (tracked by SkillComplianceRail), this rail looks up
the ``skill_envs`` configuration for the active skill and merges the
corresponding environment variables into ``tool_args["env"]`` before the
tool is executed.  The subprocess helpers in ``command_tools`` then merge
those variables into a copy of ``os.environ`` and pass them to
``subprocess.run`` / ``subprocess.Popen``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail import (
    get_session_active_skill,
    resolve_skill_session_id,
)
from jiuwenclaw.agentserver.permissions.shell_tools import SHELL_PERMISSION_TOOLS

logger = logging.getLogger(__name__)


class SkillCredentialInjectionRail(DeepAgentRail):
    """Inject per-skill credentials into shell tool calls via ``tool_args["env"]``.

    ``priority = 5`` ensures this rail runs *before*
    ``PermissionInterruptRail`` (priority=90) so that environment variables
    are already in place when permission checks execute.
    """

    priority = 5

    def __init__(
        self,
        skill_envs: dict[str, dict[str, str]] | None = None,
        preset_session_id: str | None = None,
    ) -> None:
        super().__init__()
        self._skill_envs: dict[str, dict[str, str]] = skill_envs or {}
        self._preset_session_id = preset_session_id

    # ── Hot-reload ─────────────────────────────────────────────

    def update_skill_envs(self, new_skill_envs: dict[str, dict[str, str]]) -> None:
        """Replace the internal ``_skill_envs`` mapping (called during ``reload_agent_config``)."""
        self._skill_envs = new_skill_envs or {}
        logger.info(
            "[SkillCredentialInjectionRail] skill_envs updated: skills=[%s]",
            ", ".join(self._skill_envs.keys()),
        )

    def get_skill_envs(self) -> dict[str, dict[str, str]]:
        """Return the current ``skill_envs`` mapping (public read accessor)."""
        return self._skill_envs

    # ── Core hook ──────────────────────────────────────────────

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

        credentials = self._get_skill_envs(active_skill)
        if not credentials:
            return

        self._inject_credentials(ctx.inputs, credentials)
        logger.debug(
            "[SkillCredentialInjectionRail] injected env keys=%s for skill=%s tool=%s",
            list(credentials.keys()),
            active_skill,
            tool_name,
        )

    # ── Internals ──────────────────────────────────────────────

    def _resolve_session_id(self, ctx: AgentCallbackContext) -> str:
        return resolve_skill_session_id(ctx, self._preset_session_id)

    def _get_skill_envs(self, skill_name: str) -> dict[str, str]:
        return self._skill_envs.get(skill_name, {})

    def _inject_credentials(self, inputs: ToolCallInputs, credentials: dict[str, str]) -> None:
        """Merge *credentials* into ``inputs.tool_args["env"]`` without overwriting existing keys.

        At runtime ``tool_args`` may arrive as a JSON-encoded string (the raw
        ``ToolCall.arguments`` produced by the LLM). We parse it into a dict,
        inject the credentials, and write the dict back to ``inputs.tool_args``
        so that ``AbilityManager._railed_execute_single_tool_call`` propagates
        the mutation back into ``tool_call.arguments`` for downstream tool dispatch.
        """
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
                if key not in env:
                    env[key] = value
            tool_args["env"] = env
