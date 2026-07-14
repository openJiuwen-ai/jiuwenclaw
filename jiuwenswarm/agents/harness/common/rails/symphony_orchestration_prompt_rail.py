# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Prompt rail for Symphony orchestration guidance."""

from __future__ import annotations

from typing import Any, Callable

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

_ConfigBaseProvider = (
    dict[str, Any] | Callable[[], dict[str, Any] | None] | None
)


def _render_symphony_orchestration_prompt(
    config_base: dict[str, Any] | None = None,
) -> str:
    try:
        from jiuwenswarm.symphony.config import load_symphony_config

        config = (
            load_symphony_config()
            if config_base is None
            else load_symphony_config(config_base)
        )
        if not config.enabled:
            return ""
    except Exception:
        return ""
    return """
## Symphony Orchestration

When the user says to use skill(s) or 技能, or when you judge that skill
capabilities, skill chaining, skill ordering, or a specialized toolchain could
help complete the task, you MUST call `symphony_compose_score` with the original
user task as `query` before answering.
When installed-skill retrieval is available and can narrow the search space,
use `skill_branch_peek` / `skill_branch_explore` to shortlist candidate skills
first, then pass the selected `worker_id` values as
`symphony_compose_score.candidate_skill_ids`. Do not inspect skill folders
manually or choose the execution chain yourself; Symphony owns ordering and
graph composition. After it returns, present its returned `content` directly to
the user. If Symphony reports missing inputs, ask for those inputs.

If Symphony reports no suitable candidates, a missing capability, or caveats
that point to a skill gap, use `search_skill` to discover external skills. When
installing a discovered skill is appropriate, call `install_skill`; after a
successful install, call `symphony_refresh_score` and then call
`symphony_compose_score` again with the original user task.

For clearly ordinary tasks that do not benefit from skill capabilities, continue
normally without Symphony.
"""


class SymphonyOrchestrationPromptRail(DeepAgentRail):
    """Inject Symphony orchestration guidance only when the tool is available."""

    priority = 98
    SECTION_NAME = "symphony_orchestration"
    SECTION_PRIORITY = 42
    COMPOSE_TOOL_NAME = "symphony_compose_score"

    def __init__(self, *, config_base: _ConfigBaseProvider = None) -> None:
        super().__init__()
        self._config_base = config_base
        self.system_prompt_builder = None

    def init(self, agent: Any) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent: Any) -> None:
        _ = agent
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
        self.system_prompt_builder = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if self.system_prompt_builder is None:
            return

        if not self._has_compose_tool(ctx):
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
            return

        language = getattr(self.system_prompt_builder, "language", "cn") or "cn"
        content = _render_symphony_orchestration_prompt(
            self._resolve_config_base(),
        )
        if not content.strip():
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
            return

        self.system_prompt_builder.add_section(
            PromptSection(
                name=self.SECTION_NAME,
                content={language: content},
                priority=self.SECTION_PRIORITY,
            )
        )

    @classmethod
    def _has_compose_tool(cls, ctx: AgentCallbackContext) -> bool:
        inputs = getattr(ctx, "inputs", None)
        tools = getattr(inputs, "tools", None)
        if not tools:
            return False
        return any(
            cls._model_tool_name(tool) == cls.COMPOSE_TOOL_NAME
            for tool in tools
        )

    @staticmethod
    def _model_tool_name(tool: Any) -> str:
        if isinstance(tool, dict):
            function = tool.get("function")
            if isinstance(function, dict):
                return str(function.get("name", "") or "")
            return str(tool.get("name", "") or "")
        function = getattr(tool, "function", None)
        if isinstance(function, dict):
            return str(function.get("name", "") or "")
        function_name = getattr(function, "name", None)
        if function_name is not None:
            return str(function_name)
        name = getattr(tool, "name", None)
        if name is not None:
            return str(name)
        card = getattr(tool, "card", None)
        return str(getattr(card, "name", "") or "")

    def _resolve_config_base(self) -> dict[str, Any] | None:
        if callable(self._config_base):
            return self._config_base()
        return self._config_base


__all__ = ["SymphonyOrchestrationPromptRail"]
