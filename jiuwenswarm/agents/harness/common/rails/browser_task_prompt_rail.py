# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Load-aware ``task_tool`` prompt extension for browser delegation."""

from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.subagent import SubagentRail

from jiuwenswarm.agents.harness.common.prompt.browser_task_prompt import (
    build_browser_task_prompt,
)


class BrowserTaskPromptRail(SubagentRail):
    """Append browser policy only when the browser subagent is available."""

    def _task_prompt_extension(
        self,
        ctx: AgentCallbackContext,
        language: str,
    ) -> str | None:
        if not self._has_browser_agent(ctx.agent):
            return None
        return build_browser_task_prompt(language)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Add browser routing to the task section after the base rail builds it."""

        await super().before_model_call(ctx)
        if self.system_prompt_builder is None:
            return

        language = self.system_prompt_builder.language
        extension = self._task_prompt_extension(ctx, language)
        section = self.system_prompt_builder.get_section(SectionName.TASK_TOOL)
        if extension is None or section is None:
            return

        content = section.render(language).rstrip()
        section.content[language] = f"{content}\n\n{extension}"

    def _has_browser_agent(self, agent: object) -> bool:
        deep_config = getattr(agent, "deep_config", None)
        subagents = getattr(deep_config, "subagents", None) or []
        return any(
            self._extract_agent_meta(spec)[0] == "browser_agent"
            for spec in subagents
        )


__all__ = ["BrowserTaskPromptRail"]
