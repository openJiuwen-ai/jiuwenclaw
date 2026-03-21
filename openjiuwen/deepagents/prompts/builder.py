# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Section-based system prompt builder for DeepAgent."""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional


SUPPORTED_LANGUAGES: tuple[str, ...] = ("cn", "en")
"""All languages the prompt system supports.

Add new language codes here to extend multilingual coverage.
Every ToolMetadataProvider, PromptSection, and i18n dict
must provide content for each language in this tuple.
"""

DEFAULT_LANGUAGE: str = "cn"
"""Fallback language when no explicit choice is made."""


class PromptMode(str, Enum):
    """Prompt assembly mode."""
    FULL = "full"
    MINIMAL = "minimal"
    NONE = "none"


class PromptSection:
    """A single prompt section with multilingual content."""

    def __init__(
        self,
        name: str,
        content: Dict[str, str],
        priority: int = 100,
    ):
        self.name = name
        self.content: Dict[str, str] = dict(content)
        self.priority = priority

    def render(self, language: str = "cn") -> str:
        return self.content.get(
            language,
            self.content.get(DEFAULT_LANGUAGE, ""),
        )

    def char_count(self, language: str = "cn") -> int:
        return len(self.render(language))


class SystemPromptBuilder:
    """Section-based system prompt builder.

    Designed to be a persistent instance on DeepAgent.
    Static sections are registered once at init,
    dynamic sections are updated before each build().
    """

    _MINIMAL_SECTIONS = frozenset({
        "identity",
        "safety",
        "skills",
        "tools",
        "runtime",
    })

    def __init__(
        self,
        language: str = "cn",
        mode: PromptMode = PromptMode.FULL,
    ):
        self.language = language
        self.mode = mode
        self._sections: Dict[str, PromptSection] = {}

    def add_section(self, section: PromptSection) -> "SystemPromptBuilder":
        """Add or replace a section (same name overwrites)."""
        self._sections[section.name] = section
        return self

    def remove_section(self, name: str) -> "SystemPromptBuilder":
        """Remove a section by name."""
        self._sections.pop(name, None)
        return self

    def get_all_sections(self) -> Dict[str, "PromptSection"]:
        """Return a copy of all registered sections."""
        return dict(self._sections)

    def has_section(self, name: str) -> bool:
        return name in self._sections

    def get_section(self, name: str) -> Optional[PromptSection]:
        return self._sections.get(name)

    def build(self) -> str:
        """Full rebuild: sort all sections by priority and join.

        Safe to call multiple times. Each call produces a complete
        prompt from the current state of all registered sections.
        """
        if self.mode == PromptMode.NONE:
            identity = self._sections.get("identity")
            return identity.render(self.language) if identity else ""

        sections = self._get_mode_sections()
        sorted_sections = sorted(sections, key=lambda s: s.priority)
        parts = [s.render(self.language) for s in sorted_sections]
        return "\n\n".join(part for part in parts if part.strip())

    def build_report(self) -> "PromptReport":
        """Return a diagnostic report for the current builder state."""
        from openjiuwen.deepagents.prompts.report import PromptReport
        return PromptReport.from_builder(self)

    def _get_mode_sections(self) -> List[PromptSection]:
        """Filter sections based on current mode."""
        if self.mode == PromptMode.FULL:
            return list(self._sections.values())
        return [
            s for s in self._sections.values()
            if s.name in self._MINIMAL_SECTIONS
        ]
