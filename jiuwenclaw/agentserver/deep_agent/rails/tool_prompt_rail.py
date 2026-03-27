# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""ToolPromptRail — dynamically injects the tool prompt section before each model call."""
from __future__ import annotations

from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.deepagents.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.prompt_builder import _tool_prompt


class ToolPromptRail(DeepAgentRail):
    """Rail that injects the tool prompt section into system prompt.

    Moves the tool listing out of the static identity baseline and into
    a dynamic ``before_model_call`` hook, consistent with how SkillRail
    and TaskPlanningRail inject their sections.
    """

    priority = 85

    def __init__(self, mode: str = "agent", language: str = "cn") -> None:
        super().__init__()
        self.mode = mode
        self.language = language
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject tool prompt section before model call."""
        if self.system_prompt_builder is None:
            return

        tool_section = _tool_prompt(self.mode, self.language)
        self.system_prompt_builder.add_section(PromptSection(name="tools", content={f"{self.language}": tool_section}))

    def set_mode(self, mode: str) -> None:
        """Update the prompt mode (e.g. 'plan' or 'agent')."""
        self.mode = mode


__all__ = [
    "ToolPromptRail",
]
