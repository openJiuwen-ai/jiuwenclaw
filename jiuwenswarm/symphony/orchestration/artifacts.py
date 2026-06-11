"""Load Score artifacts for Skill orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ScoreArtifacts:
    """Offline Score artifacts needed by orchestration."""

    score_dir: Path
    manifest: dict[str, Any]
    skills: list[dict[str, Any]]
    graph: dict[str, Any]
    lookup: dict[str, Any]
    io_name_vocab: dict[str, Any] | None = None

    @property
    def skill_by_id(self) -> dict[str, dict[str, Any]]:
        return {skill["id"]: skill for skill in self.skills if skill.get("id")}


def load_score_artifacts(score_dir: str | Path) -> ScoreArtifacts:
    """Load Score artifacts through score_manifest.json."""

    root = Path(score_dir).resolve()
    manifest = _read_json(root / "score_manifest.json")
    artifacts = manifest.get("artifacts", {})
    skills_payload = _read_json(root / artifacts.get("skills", "skills.json"))
    graph = _read_json(root / artifacts.get("graph", "skill_graph.json"))
    lookup = _read_json(root / artifacts.get("score_lookup", "score_lookup.json"))
    io_name_vocab_path = root / artifacts.get("io_name_vocab", "io_name_vocab.json")
    return ScoreArtifacts(
        score_dir=root,
        manifest=manifest,
        skills=skills_payload.get("skills", []),
        graph=graph,
        lookup=lookup,
        io_name_vocab=_read_optional_json(io_name_vocab_path),
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing score artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _read_optional_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
