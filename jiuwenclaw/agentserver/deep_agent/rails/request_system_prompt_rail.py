# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Inject request-scoped system prompt content into DeepAgent prompts."""

from __future__ import annotations

from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail


class RequestSystemPromptRail(DeepAgentRail):
    """Adds a per-request system prompt section without persisting it across requests."""

    priority = 5
    SECTION_NAME = "request_system_prompt"

    def __init__(self) -> None:
        super().__init__()
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
        self.system_prompt_builder = None

    @staticmethod
    def _extract_prompt(inputs: Any) -> str:
        if not isinstance(inputs, dict):
            return ""
        value = inputs.get("system_prompt_append")
        return value.strip() if isinstance(value, str) else ""

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self.system_prompt_builder:
            return

        self.system_prompt_builder.remove_section(self.SECTION_NAME)
        prompt = self._extract_prompt(ctx.inputs)
        if not prompt:
            return

        self.system_prompt_builder.add_section(
            PromptSection(
                name=self.SECTION_NAME,
                content={"cn": prompt, "en": prompt},
                priority=20,
            )
        )
