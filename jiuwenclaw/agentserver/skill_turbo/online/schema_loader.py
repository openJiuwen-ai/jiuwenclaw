# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Turbo 产物探测 + schema 加载 + frontmatter 解析。

职责（设计 §5.1 / §8.5）：
1. 探测共享 skill registry 中各 skill 的 ``<skill_root>/turbo/SKILL_TURBO.md``
2. 解析 SKILL_TURBO.md frontmatter（name/description/source_skill）
3. 加载 ``schema_<scenario>.json``
4. 读取 SKILL_TURBO.md 正文（层2 active-skill-body 用）

不引入 yaml 依赖：frontmatter 仅含顶层标量字段，手写简易解析。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# schema JSON 最大字节数（防恶意超大文件）
_MAX_SCHEMA_BYTES = 2_000_000


@dataclass(frozen=True)
class TurboFace:
    """一个 turbo 加速面的元信息（层1 catalog 用）。

    与源 skill 同根（colocated）：``skill_root`` 是源 skill 根目录，
    ``turbo_dir`` 是其下的 ``turbo/`` 子目录。
    """

    turbo_name: str            # frontmatter name，如 "pptx-craft_turbo"
    description: str           # frontmatter description
    source_skill: str          # frontmatter source_skill，如 "pptx-craft"
    skill_root: str            # 源 skill 根目录绝对路径
    turbo_dir: str             # turbo/ 子目录绝对路径
    scenarios: tuple[str, ...]  # 可用切面列表，如 ("create_ppt",)


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """解析 SKILL_TURBO.md 的 YAML frontmatter（``---`` 包围）。

    返回 dict（name/description/source_skill 等顶层标量字段）或 None（无 frontmatter）。

    只解析顶层 ``key: value`` 标量字段，不处理嵌套/列表。description 去掉包围引号。
    """
    if not text:
        return None
    # frontmatter 必须以 --- 开头
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return None
    lines = stripped.splitlines()
    # 第一行是 ---，找下一个 ---
    if len(lines) < 2:
        return None
    end_idx = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx == -1:
        return None
    fm_lines = lines[1:end_idx]
    result: dict[str, str] = {}
    for line in fm_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # 去掉包围引号（单/双引号）
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key:
            result[key] = value
    return result if result else None


def _strip_frontmatter(text: str) -> str:
    """去掉 frontmatter 部分，返回正文。无 frontmatter 时原样返回。"""
    if not text:
        return text
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return text
    lines = stripped.splitlines()
    end_idx = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx == -1:
        return text
    # 保留正文部分（end_idx+1 起），并保留原 text 的前导空白差异最小化
    body = "\n".join(lines[end_idx + 1:])
    return body.lstrip("\n")


def discover_turbo_face(skill_root: str) -> TurboFace | None:
    """探测 ``<skill_root>/turbo/SKILL_TURBO.md``，解析 frontmatter + 扫描 schema_*.json。

    不存在或解析失败返回 None。frontmatter 缺 name/source_skill 也返回 None。
    """
    if not skill_root:
        return None
    root_path = Path(skill_root).expanduser().resolve()
    turbo_dir = root_path / "turbo"
    skill_turbo_md = turbo_dir / "SKILL_TURBO.md"
    if not skill_turbo_md.is_file():
        return None
    try:
        text = skill_turbo_md.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "[TurboSchemaLoader] read SKILL_TURBO.md failed skill_root=%s err=%s",
            skill_root, exc,
        )
        return None
    fm = parse_frontmatter(text)
    if fm is None:
        logger.warning(
            "[TurboSchemaLoader] no frontmatter in SKILL_TURBO.md skill_root=%s",
            skill_root,
        )
        return None
    turbo_name = fm.get("name", "").strip()
    source_skill = fm.get("source_skill", "").strip()
    description = fm.get("description", "").strip()
    if not turbo_name or not source_skill:
        logger.warning(
            "[TurboSchemaLoader] frontmatter missing name/source_skill skill_root=%s fm=%s",
            skill_root, fm,
        )
        return None
    # 扫描 schema_*.json 得切面列表
    scenarios: list[str] = []
    try:
        for schema_file in sorted(turbo_dir.glob("schema_*.json")):
            stem = schema_file.stem  # schema_create_ppt
            scenario = stem[len("schema_"):] if stem.startswith("schema_") else stem
            if scenario:
                scenarios.append(scenario)
    except OSError as exc:
        logger.warning(
            "[TurboSchemaLoader] glob schema_*.json failed turbo_dir=%s err=%s",
            turbo_dir, exc,
        )
    if not scenarios:
        logger.warning(
            "[TurboSchemaLoader] no schema_*.json found turbo_dir=%s", turbo_dir,
        )
        return None
    return TurboFace(
        turbo_name=turbo_name,
        description=description,
        source_skill=source_skill,
        skill_root=str(root_path),
        turbo_dir=str(turbo_dir),
        scenarios=tuple(scenarios),
    )


def _iter_candidate_skill_roots(entry: Path) -> list[Path]:
    """把 registry 目录展开为待探测的 skill 根目录列表。

    兼容两种注入形态（与 ``_resolve_skill_root_for_turbo`` 一致）：
    1. 目录本身就是 skill 根（含 ``turbo/SKILL_TURBO.md`` 或仅 SKILL.md）
    2. skills 父目录（如 ``office-claw-skills/``，子目录才是各 skill）
       —— ``JIUWENCLAW_SHARED_SKILLS_DIRS`` 经 RelayClaw 注入时为此形态
    """
    if not entry.is_dir():
        return []
    # 自身即 skill 根（有 turbo 或有 SKILL.md）→ 只探测自身，不再扫子目录
    if (entry / "turbo" / "SKILL_TURBO.md").is_file() or (entry / "SKILL.md").is_file():
        return [entry]
    # 父目录：扫一层直接子目录（跳过隐藏名 / 非目录）
    children: list[Path] = []
    try:
        for child in sorted(entry.iterdir()):
            if not child.is_dir():
                continue
            name = child.name
            if name.startswith(".") or name in {"__pycache__", "refs", "node_modules"}:
                continue
            children.append(child)
    except OSError as exc:
        logger.warning(
            "[TurboSchemaLoader] list skill children failed dir=%s err=%s",
            entry, exc,
        )
        return []
    return children


def discover_all_turbo_faces(skill_roots: list[str]) -> list[TurboFace]:
    """遍历 registry 目录，返回所有有 turbo 产物的 TurboFace 列表。

    ``skill_roots`` 来自 ``get_agent_registered_skill_dirs()``，可能是：
    - skill 根目录本身
    - skills 父目录（``office-claw-skills``）

    对父目录会展开一层子 skill 再探测；按最终 ``TurboFace.skill_root`` 去重。
    """
    if not skill_roots:
        return []
    seen: set[str] = set()
    faces: list[TurboFace] = []
    for raw in skill_roots:
        if not raw:
            continue
        try:
            entry = Path(raw).expanduser().resolve()
        except OSError:
            continue
        if not entry.exists():
            continue
        for candidate in _iter_candidate_skill_roots(entry):
            key = str(candidate.resolve())
            if key in seen:
                continue
            seen.add(key)
            face = discover_turbo_face(key)
            if face is not None:
                faces.append(face)
    return faces


def load_schema(turbo_dir: str, scenario: str) -> dict[str, Any]:
    """加载 ``<turbo_dir>/schema_<scenario>.json``。

    Raises:
        FileNotFoundError: 文件不存在。
        json.JSONDecodeError: JSON 解析失败。
        ValueError: scenario 非法或文件过大。
    """
    from jiuwenclaw.agentserver.skill_turbo.online.skill_name_guard import (
        InvalidScenarioError,
        validate_scenario,
    )

    try:
        scenario = validate_scenario(scenario)
    except InvalidScenarioError as exc:
        raise ValueError(str(exc)) from exc

    turbo_root = Path(turbo_dir).resolve()
    schema_path = (turbo_root / f"schema_{scenario}.json").resolve()
    try:
        schema_path.relative_to(turbo_root)
    except ValueError as exc:
        raise ValueError(f"schema 路径逃逸 turbo_dir: {schema_path}") from exc
    if not schema_path.is_file():
        raise FileNotFoundError(f"schema not found: {schema_path}")
    size = schema_path.stat().st_size
    if size > _MAX_SCHEMA_BYTES:
        raise ValueError(
            f"schema 文件过大 ({size} bytes > {_MAX_SCHEMA_BYTES}): {schema_path}"
        )
    text = schema_path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"schema must be a JSON object: {schema_path}")
    return data


def load_skill_turbo_body(turbo_dir: str) -> str:
    """读取 ``<turbo_dir>/SKILL_TURBO.md`` 全文并去掉 frontmatter，返回正文。

    层2 active-skill-body 用：Agent 据正文推理下一个 PlanTask。
    """
    skill_turbo_md = Path(turbo_dir) / "SKILL_TURBO.md"
    if not skill_turbo_md.is_file():
        raise FileNotFoundError(f"SKILL_TURBO.md not found: {skill_turbo_md}")
    text = skill_turbo_md.read_text(encoding="utf-8")
    return _strip_frontmatter(text)


__all__ = [
    "TurboFace",
    "parse_frontmatter",
    "discover_turbo_face",
    "discover_all_turbo_faces",
    "load_schema",
    "load_skill_turbo_body",
]
