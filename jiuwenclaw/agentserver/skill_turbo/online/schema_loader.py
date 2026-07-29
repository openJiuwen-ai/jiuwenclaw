# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Turbo 面发现 + schema 加载 + frontmatter 解析.

职责：
1. 探查 skill registry 中各 skill 的 ``<skill_root>/<skill_name>/turbo/SKILL_TURBO.md``
2. 解析 SKILL_TURBO.md frontmatter（name / description / source_skill）
3. 加载 ``schema_<scenario>.json``
4. 读取 SKILL_TURBO.md 正文（供 active-skill-body 钉入）
5. 格式化 execution_flow 概览（供 activate 返回）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jiuwenclaw.utils import logger

__all__ = [
    "TurboFace",
    "parse_frontmatter",
    "discover_turbo_face",
    "discover_all_turbo_faces",
    "load_schema",
    "load_skill_turbo_body",
    "format_execution_flow_overview",
]


# ─────────────────────────────────────────────────────────────────────────────
# TurboFace — turbo 加速面身份
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TurboFace:
    """turbo 加速面身份信息（从 SKILL_TURBO.md frontmatter 解析）."""

    turbo_name: str          # e.g. "pptx-craft_turbo"
    source_skill: str        # e.g. "pptx-craft"
    turbo_dir: str           # e.g. "/path/to/skills/pptx-craft/turbo"
    description: str = ""    # frontmatter description


# ─────────────────────────────────────────────────────────────────────────────
# frontmatter 解析
# ─────────────────────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_KV_RE = re.compile(r'^(\w+)\s*:\s*"?(.*?)"?\s*$', re.MULTILINE)


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """解析 YAML frontmatter（轻量，不依赖 pyyaml）.

    Args:
        text: SKILL_TURBO.md 全文

    Returns:
        frontmatter 字段 dict，无 frontmatter 返回 None
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None
    fm_text = match.group(1)
    result: dict[str, str] = {}
    for m in _KV_RE.finditer(fm_text):
        result[m.group(1)] = m.group(2)
    return result if result else None


def _strip_frontmatter(text: str) -> str:
    """去掉 frontmatter，返回正文."""
    match = _FRONTMATTER_RE.match(text)
    if match:
        return text[match.end():]
    return text


# ─────────────────────────────────────────────────────────────────────────────
# turbo 面发现
# ─────────────────────────────────────────────────────────────────────────────


def _iter_candidate_skill_roots() -> list[Path]:
    """获取候选 skill 根目录列表（通过 jiuwenclaw utils）."""
    try:
        from jiuwenclaw.utils import get_agent_registered_skill_dirs

        return list(get_agent_registered_skill_dirs())
    except Exception as exc:
        logger.debug("[schema_loader] get_agent_registered_skill_dirs unavailable: %s", exc)
        return []


def discover_turbo_face(skill_root: str) -> TurboFace | None:
    """发现指定 skill_root 下的 turbo 面.

    Args:
        skill_root: skill 根目录路径（包含多个 skill 子目录的目录，
                     或直接包含 turbo/ 的 skill 目录）

    Returns:
        TurboFace 或 None（未找到）
    """
    root_path = Path(skill_root)

    # 候选路径：skill_root 可能是 skills/ 目录（含多个 skill）或单个 skill 目录
    candidates: list[Path] = []
    if (root_path / "turbo" / "SKILL_TURBO.md").exists():
        candidates.append(root_path)
    # 也检查子目录
    for child in root_path.iterdir():
        if child.is_dir() and (child / "turbo" / "SKILL_TURBO.md").exists():
            candidates.append(child)

    for skill_dir in candidates:
        turbo_dir = skill_dir / "turbo"
        skill_turbo_md = turbo_dir / "SKILL_TURBO.md"
        try:
            text = skill_turbo_md.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("[schema_loader] read SKILL_TURBO.md failed: %s", exc)
            continue

        fm = parse_frontmatter(text)
        if not fm:
            continue

        turbo_name = fm.get("name", "")
        source_skill = fm.get("source_skill", skill_dir.name)
        description = fm.get("description", "")

        if not turbo_name:
            continue

        return TurboFace(
            turbo_name=turbo_name,
            source_skill=source_skill,
            turbo_dir=str(turbo_dir.resolve()),
            description=description,
        )

    return None


def discover_all_turbo_faces(skill_roots: list[str] | None = None) -> list[TurboFace]:
    """发现所有已注册 skill 下的 turbo 面.

    Args:
        skill_roots: 候选 skill 根目录列表；None 时自动发现

    Returns:
        TurboFace 列表
    """
    roots = [Path(r) for r in skill_roots] if skill_roots else _iter_candidate_skill_roots()
    faces: list[TurboFace] = []
    for root in roots:
        face = discover_turbo_face(str(root))
        if face is not None:
            faces.append(face)
    return faces


# ─────────────────────────────────────────────────────────────────────────────
# schema 加载
# ─────────────────────────────────────────────────────────────────────────────


def load_schema(turbo_dir: str, scenario: str) -> dict[str, Any]:
    """加载 schema_<scenario>.json.

    Args:
        turbo_dir: turbo/ 目录路径
        scenario: 切面名，如 "create_ppt"

    Returns:
        schema dict

    Raises:
        FileNotFoundError: schema 文件不存在
        json.JSONDecodeError: JSON 解析失败
    """
    schema_path = Path(turbo_dir) / f"schema_{scenario}.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"schema not found: {schema_path}")
    text = schema_path.read_text(encoding="utf-8")
    return json.loads(text)


def load_skill_turbo_body(turbo_dir: str) -> str:
    """读取 SKILL_TURBO.md 正文（去掉 frontmatter）.

    Args:
        turbo_dir: turbo/ 目录路径

    Returns:
        SKILL_TURBO.md 正文（不含 frontmatter）
    """
    md_path = Path(turbo_dir) / "SKILL_TURBO.md"
    text = md_path.read_text(encoding="utf-8")
    return _strip_frontmatter(text)


# ─────────────────────────────────────────────────────────────────────────────
# execution_flow 概览格式化
# ─────────────────────────────────────────────────────────────────────────────


def format_execution_flow_overview(schema: dict[str, Any]) -> str:
    """格式化 execution_flow 为人类可读概览字符串.

    Args:
        schema: schema dict

    Returns:
        如 "bootstrap → request_params → doc_parse(条件) → ... → deliver"
    """
    flow = schema.get("execution_flow", [])
    parts: list[str] = []
    seen: set[str] = set()
    for step in flow:
        plan_name = step.get("plan_name", "")
        if not plan_name or plan_name in seen:
            continue
        seen.add(plan_name)
        when = step.get("when")
        if when:
            parts.append(f"{plan_name}(条件:{when})")
        else:
            parts.append(plan_name)
    return " → ".join(parts)
