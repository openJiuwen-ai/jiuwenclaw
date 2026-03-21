# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt diagnostic report."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from openjiuwen.deepagents.prompts.builder import SystemPromptBuilder

# Rough estimate: 1 token ≈ 2.5 Chinese chars or 4 English chars.
_CN_CHARS_PER_TOKEN = 2.5
_EN_CHARS_PER_TOKEN = 4.0


@dataclass
class SectionInfo:
    """Lightweight snapshot of a single section."""
    name: str
    priority: int
    char_count: int


@dataclass
class PromptReport:
    """Diagnostic report for a built system prompt."""
    total_chars: int
    estimated_tokens: int
    section_count: int
    sections: List[SectionInfo] = field(default_factory=list)
    mode: str = "full"
    language: str = "cn"

    @classmethod
    def from_builder(cls, builder: "SystemPromptBuilder") -> "PromptReport":
        """Create a report from the current state of a builder."""
        language = builder.language
        mode = builder.mode.value

        section_infos: List[SectionInfo] = []
        total_chars = 0
        for s in sorted(builder.get_all_sections().values(), key=lambda x: x.priority):
            chars = s.char_count(language)
            section_infos.append(SectionInfo(
                name=s.name,
                priority=s.priority,
                char_count=chars,
            ))
            total_chars += chars

        chars_per_token = (
            _CN_CHARS_PER_TOKEN if language == "cn" else _EN_CHARS_PER_TOKEN
        )
        estimated_tokens = int(total_chars / chars_per_token) if total_chars else 0

        return cls(
            total_chars=total_chars,
            estimated_tokens=estimated_tokens,
            section_count=len(section_infos),
            sections=section_infos,
            mode=mode,
            language=language,
        )

    def to_dict(self) -> Dict:
        """Serialize to a plain dict."""
        return {
            "total_chars": self.total_chars,
            "estimated_tokens": self.estimated_tokens,
            "section_count": self.section_count,
            "sections": [
                {"name": s.name, "priority": s.priority, "char_count": s.char_count}
                for s in self.sections
            ],
            "mode": self.mode,
            "language": self.language,
        }

    def summary(self) -> str:
        """Human-readable one-line summary."""
        return (
            f"[PromptReport] mode={self.mode} lang={self.language} "
            f"sections={self.section_count} chars={self.total_chars} "
            f"est_tokens≈{self.estimated_tokens}"
        )
