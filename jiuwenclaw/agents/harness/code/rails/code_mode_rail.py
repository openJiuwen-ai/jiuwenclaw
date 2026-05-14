# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""CodeModePromptRail — Inject Code-mode-specific prompt sections before each model call.

This rail is registered only in Code mode (interface_code.py).
It injects 7 sections: system, safety_enhanced, doing_tasks,
tool_discipline, actions_with_care, tone_and_style, output_efficiency.
"""
from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agents.harness.common.prompt.prompt_builder import (
    LocalSectionName,
    _system_prompt,
    _safety_enhanced_prompt,
    _doing_tasks_prompt,
    _tool_discipline_prompt,
    _actions_with_care_prompt,
    _tone_and_style_prompt,
    _output_efficiency_prompt,
)

_INJECTED_SECTION_NAMES = [
    LocalSectionName.SYSTEM,
    LocalSectionName.SAFETY_ENHANCED,
    LocalSectionName.DOING_TASKS,
    LocalSectionName.TOOL_DISCIPLINE,
    LocalSectionName.ACTIONS_WITH_CARE,
    LocalSectionName.TONE_AND_STYLE,
    LocalSectionName.OUTPUT_EFFICIENCY,
]

_INJECTED_SECTION_GENERATORS = [
    _system_prompt,
    _safety_enhanced_prompt,
    _doing_tasks_prompt,
    _tool_discipline_prompt,
    _actions_with_care_prompt,
    _tone_and_style_prompt,
    _output_efficiency_prompt,
]


class CodeModePromptRail(DeepAgentRail):
    """Inject all Code-mode-specific prompt sections."""

    priority = 5

    def __init__(self) -> None:
        super().__init__()
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        if self.system_prompt_builder is not None:
            for name in _INJECTED_SECTION_NAMES:
                self.system_prompt_builder.remove_section(name)
        self.system_prompt_builder = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if self.system_prompt_builder is None:
            return

        language = self.system_prompt_builder.language or "cn"
        for generator in _INJECTED_SECTION_GENERATORS:
            self.system_prompt_builder.add_section(generator(language))
