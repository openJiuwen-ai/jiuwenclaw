# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent-specific prompt builder extensions."""
from __future__ import annotations

from enum import Enum
from typing import List

from openjiuwen.core.single_agent.prompts.builder import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    PromptSection,
    SystemPromptBuilder as BaseSystemPromptBuilder,
)


class PromptMode(str, Enum):
    """Prompt assembly mode for DeepAgent."""
    FULL = "full"
    MINIMAL = "minimal"
    NONE = "none"


class SystemPromptBuilder(BaseSystemPromptBuilder):
    """DeepAgent prompt builder with mode filtering and diagnostics."""

    _MINIMAL_SECTIONS = frozenset({
        "identity",
        "safety",
        "skills",
        "tools",
        "task_tool",
        "runtime",
    })

    def __init__(
        self,
        language: str = DEFAULT_LANGUAGE,
        mode: PromptMode = PromptMode.FULL,
    ):
        super().__init__(language=language)
        self.mode = mode

    def build(self) -> str:
        """Build prompt according to the current DeepAgent prompt mode."""
        if self.mode == PromptMode.NONE:
            identity = self.get_section("identity")
            return identity.render(self.language) if identity else ""
        return super().build()

    def build_report(self) -> "PromptReport":  # type: ignore[name-defined]
        """Return a diagnostic report for the current builder state."""
        from openjiuwen.deepagents.prompts.report import PromptReport
        return PromptReport.from_builder(self)

    def _get_sections_for_build(self) -> List[PromptSection]:
        """Filter sections based on the configured DeepAgent prompt mode."""
        if self.mode == PromptMode.FULL:
            return super()._get_sections_for_build()
        return [
            section for section in super()._get_sections_for_build()
            if section.name in self._MINIMAL_SECTIONS
        ]


__all__ = [
    "DEFAULT_LANGUAGE",
    "PromptMode",
    "PromptSection",
    "SUPPORTED_LANGUAGES",
    "SystemPromptBuilder",
]
