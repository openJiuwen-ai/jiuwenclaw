# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Collect compact skill state for security review candidate judgment."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def collect_skill_state(skills_dir: str | Path, *, limit: int = 32) -> dict[str, Any]:
    root = Path(skills_dir)
    loaded_skills: list[dict[str, Any]] = []
    if not root.exists() or not root.is_dir():
        return {
            "loaded_skills": [],
            "known_security_skill_names": [],
            "candidate_skill_summaries": [],
        }
    for skill_path in sorted(root.iterdir(), key=lambda item: item.name):
        if len(loaded_skills) >= limit:
            break
        skill_file = skill_path / "SKILL.md"
        if not skill_file.exists() or not skill_file.is_file():
            continue
        text = skill_file.read_text(encoding="utf-8", errors="replace")
        loaded_skills.append(
            {
                "name": _metadata_value(text, "name") or skill_path.name,
                "description": _metadata_value(text, "description"),
                "security_sections": _security_sections(text),
            }
        )
    return {
        "loaded_skills": loaded_skills,
        "known_security_skill_names": [
            skill["name"] for skill in loaded_skills if _is_security_related(skill)
        ],
        "candidate_skill_summaries": [],
    }


def _metadata_value(text: str, key: str) -> str:
    prefix = f"{key}:"
    for line in text.splitlines()[:40]:
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped.split(":", 1)[1].strip().strip("\"'")
    return ""


def _security_sections(text: str) -> list[str]:
    lines = text.splitlines()
    sections: list[str] = []
    capture: list[str] = []
    capturing = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") and "security" in stripped.lower():
            if capture:
                sections.append("\n".join(capture).strip()[:1000])
            capture = [stripped]
            capturing = True
            continue
        if capturing and stripped.startswith("#"):
            sections.append("\n".join(capture).strip()[:1000])
            capture = []
            capturing = False
        if capturing:
            capture.append(line)
    if capture:
        sections.append("\n".join(capture).strip()[:1000])
    return [section for section in sections if section]


def _is_security_related(skill: dict[str, Any]) -> bool:
    name = str(skill.get("name") or "").lower()
    description = str(skill.get("description") or "").lower()
    return (
        bool(skill.get("security_sections"))
        or "security" in name
        or "safe" in name
        or "security" in description
    )
