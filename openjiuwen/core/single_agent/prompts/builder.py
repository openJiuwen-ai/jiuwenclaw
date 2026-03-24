# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Section-based system prompt builder."""
from __future__ import annotations

from typing import Dict, List, Optional


SUPPORTED_LANGUAGES: tuple[str, ...] = ("cn", "en")
"""All languages the prompt system supports.

Add new language codes here to extend multilingual coverage.
Every ToolMetadataProvider, PromptSection, and i18n dict
must provide content for each language in this tuple.
"""

DEFAULT_LANGUAGE: str = "cn"
"""Fallback language when no explicit choice is made."""


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
    """Section-based system prompt builder base class.

    This class only provides generic section registration and rendering.
    Agent-family-specific prompt policies, such as mode switching or prompt
    diagnostics, should live in subclasses outside the single-agent layer.
    """

    def __init__(
        self,
        language: str = DEFAULT_LANGUAGE,
    ):
        self.language = language
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
        """Sort current sections by priority and join them into one prompt.

        Safe to call multiple times. Each call produces a complete
        prompt from the current state of all registered sections.
        """
        sections = self._get_sections_for_build()
        sorted_sections = sorted(sections, key=lambda s: s.priority)
        parts = [s.render(self.language) for s in sorted_sections]
        return "\n\n".join(part for part in parts if part.strip())

    def _get_sections_for_build(self) -> List[PromptSection]:
        """Return the sections that should participate in the final build."""
        return list(self._sections.values())
