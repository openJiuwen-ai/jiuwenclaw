# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Data model definitions for skill evolution system."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

VALID_SECTIONS = {"Instructions", "Examples", "Troubleshooting"}


@dataclass
class EvolutionChange:
    section: str    # "Instructions" | "Examples" | "Troubleshooting"
    action: str     # Fixed to "append"
    content: str    # Markdown content to append
    relevant: bool = True  # Whether the signal is relevant to the Skill

    def to_dict(self) -> dict:
        return {
            "section": self.section,
            "action": self.action,
            "content": self.content,
            "relevant": self.relevant,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EvolutionChange":
        return cls(
            section=d.get("section", "Troubleshooting"),
            action=d.get("action", "append"),
            content=d.get("content", ""),
            relevant=d.get("relevant", True),
        )


@dataclass
class EvolutionEntry:
    id: str                    # "ev_xxxxxxxx"
    source: str                # "execution_failure" | "user_correction" | "repeated_failure"
    timestamp: str             # ISO 8601
    context: str               # Signal summary
    change: EvolutionChange
    applied: bool = False      # False = pending, True = solidified

    @classmethod
    def make(
        cls,
        source: str,
        context: str,
        change: EvolutionChange,
    ) -> "EvolutionEntry":
        return cls(
            id=f"ev_{uuid.uuid4().hex[:8]}",
            source=source,
            timestamp=datetime.now(tz=timezone.utc).isoformat(),
            context=context,
            change=change,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "timestamp": self.timestamp,
            "context": self.context,
            "change": self.change.to_dict(),
            "applied": self.applied,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EvolutionEntry":
        return cls(
            id=d.get("id", f"ev_{uuid.uuid4().hex[:8]}"),
            source=d.get("source", "unknown"),
            timestamp=d.get("timestamp", ""),
            context=d.get("context", ""),
            change=EvolutionChange.from_dict(d.get("change", {})),
            applied=d.get("applied", False),
        )

    @property
    def is_pending(self) -> bool:
        return not self.applied


@dataclass
class EvolutionFile:
    skill_id: str
    version: str = "1.0.0"
    updated_at: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )
    entries: List[EvolutionEntry] = field(default_factory=list)

    @property
    def pending_entries(self) -> List[EvolutionEntry]:
        return [e for e in self.entries if e.is_pending]

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "updated_at": self.updated_at,
            "entries": [e.to_dict() for e in self.entries],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EvolutionFile":
        entries = [EvolutionEntry.from_dict(e) for e in d.get("entries", [])]
        return cls(
            skill_id=d.get("skill_id", ""),
            version=d.get("version", "1.0.0"),
            updated_at=d.get("updated_at", ""),
            entries=entries,
        )

    @classmethod
    def empty(cls, skill_id: str) -> "EvolutionFile":
        return cls(skill_id=skill_id)


@dataclass
class EvolutionSignal:
    type: str                      # "execution_failure" | "user_correction" | "repeated_failure"
    section: str                   # Recommended SKILL.md section
    excerpt: str                   # Original content summary
    tool_name: Optional[str] = None
    skill_name: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "section": self.section,
            "excerpt": self.excerpt,
            "tool_name": self.tool_name,
            "skill_name": self.skill_name,
        }
