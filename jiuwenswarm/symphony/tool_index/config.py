"""Tool index configuration — mirrors SkillRetrievalSettings for the Tool domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolIndexConfig:
    """Minimal configuration for the Tool progressive retrieval index."""

    enabled: bool = True
    artifact_root: Path = field(
        default_factory=lambda: Path.home() / ".jiuwen" / "tool_index"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "artifact_root": str(self.artifact_root),
        }


def load_tool_index_config() -> ToolIndexConfig:
    """Load tool index configuration.

    In the full version this reads from config.yaml; the Day-1 version uses
    sensible defaults so we can verify the pipeline without config plumbing.
    """
    return ToolIndexConfig()
