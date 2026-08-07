# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""根据 Skill 目录结构识别 skill_type.

优先级固定：swarm_skill > multimodal_skill > skill。
扫描时排除根级 ``.archive/``。
"""

from __future__ import annotations

from pathlib import Path

from jiuwenswarm.server.runtime.skill.archive_store import ARCHIVE_DIRNAME

SKILL_TYPE_SKILL = "skill"
SKILL_TYPE_SWARM = "swarm_skill"
SKILL_TYPE_MULTIMODAL = "multimodal_skill"

_MULTIMEDIA_SUFFIXES = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".mp3",
        ".wav",
        ".mp4",
        ".mov",
    }
)


def detect_skill_type(skill_dir: Path | None) -> str:
    """识别 Skill 类型；目录无效时返回普通 ``skill``."""
    if skill_dir is None or not skill_dir.is_dir():
        return SKILL_TYPE_SKILL

    if (skill_dir / "workflow.md").is_file():
        return SKILL_TYPE_SWARM
    if (skill_dir / "role").is_dir() or (skill_dir / "roles").is_dir():
        return SKILL_TYPE_SWARM

    if _has_multimedia_asset(skill_dir):
        return SKILL_TYPE_MULTIMODAL
    return SKILL_TYPE_SKILL


def _has_multimedia_asset(skill_dir: Path) -> bool:
    archive = skill_dir / ARCHIVE_DIRNAME
    for path in skill_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            path.relative_to(archive)
            continue  # 落在 .archive 下，跳过
        except ValueError:
            pass
        if path.suffix.lower() in _MULTIMEDIA_SUFFIXES:
            return True
    return False
