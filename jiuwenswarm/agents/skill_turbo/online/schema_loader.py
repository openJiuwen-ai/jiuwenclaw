# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Turbo 面发现 + schema 加载 + frontmatter 解析.

职责：
1. 探查已注册 skill 根目录下各 skill 的 ``turbo/SKILL_TURBO.md``
   （根目录解析与 SkillUseRail 一致：共享 tip dirs 优先，否则 workspace/skills）
2. 解析 SKILL_TURBO.md frontmatter（name / description / source_skill）
3. 加载 ``schema_<scenario>.json``
4. 读取 SKILL_TURBO.md 正文（供 active-skill-body 钉入）
5. 格式化 execution_flow 概览（供 activate 返回）
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jiuwenswarm.agents.skill_turbo.online.param_validator import validate_scenario
from jiuwenswarm.common.utils import logger

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
    description: str = ""   # frontmatter description
    scenario_count: int = 0  # turbo_dir 下 schema_*.json 文件数（发现时一次性扫描，避免每次 before_model_call glob）


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

# 进程级缓存 turbo faces（TTL 60s + mtime 失效）
# key = frozenset(skill_roots)，value = (cached_at, mtime_snapshot, faces)
# discover_all_turbo_faces 和 resolve_turbo_face_for_skill 共用同一缓存
_TURBO_FACES_CACHE: dict[frozenset[str], tuple[float, dict[str, float], list[TurboFace]]] = {}
_TURBO_FACES_CACHE_TTL = 60.0  # 60s TTL


def _get_mtime_snapshot(roots: Iterable[Path]) -> dict[str, float]:
    """获取 SKILL_TURBO.md 文件的 mtime 快照（用于失效检测）."""
    snapshot: dict[str, float] = {}
    for root in roots:
        root_path = Path(root)
        # 候选路径：skill_root 可能是 skills/ 目录或单个 skill 目录
        candidates: list[Path] = []
        if (root_path / "turbo" / "SKILL_TURBO.md").exists():
            candidates.append(root_path)
        for child in root_path.iterdir():
            if child.is_dir() and (child / "turbo" / "SKILL_TURBO.md").exists():
                candidates.append(child)
        
        for skill_dir in candidates:
            md_path = skill_dir / "turbo" / "SKILL_TURBO.md"
            try:
                snapshot[str(md_path.resolve())] = md_path.stat().st_mtime
            except Exception as exc:
                logger.debug(
                    "[schema_loader] mtime snapshot skip %s: %s",
                    md_path,
                    exc,
                )
    return snapshot


def _iter_turbo_skill_dirs(root_path: Path) -> list[Path]:
    """列出 root 下具备 turbo 加速面的 skill 目录（含 root 自身）.

    只遍历一层子目录。注册结构约定为 ``<skill_root>/<skill_name>/turbo/``，
    skill 目录直接位于 skill_root 下，无嵌套。
    """
    candidates: list[Path] = []
    if (root_path / "turbo" / "SKILL_TURBO.md").exists():
        candidates.append(root_path)
    try:
        children = list(root_path.iterdir())
    except Exception as exc:
        logger.warning("[schema_loader] iter skill root failed %s: %s", root_path, exc)
        return candidates
    for child in children:
        if child.is_dir() and (child / "turbo" / "SKILL_TURBO.md").exists():
            candidates.append(child)
    return candidates


def _build_turbo_face(skill_dir: Path) -> TurboFace | None:
    """从单个 skill 目录构建 TurboFace；无效则返回 None."""
    turbo_dir = skill_dir / "turbo"
    skill_turbo_md = turbo_dir / "SKILL_TURBO.md"
    try:
        text = skill_turbo_md.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("[schema_loader] read SKILL_TURBO.md failed: %s", exc)
        return None

    fm = parse_frontmatter(text)
    if not fm:
        return None

    turbo_name = fm.get("name", "")
    if not turbo_name:
        return None

    # 一次性扫描 schema_*.json 数量，避免 before_model_call 每次重复 glob
    scenario_count = 0
    try:
        scenario_count = sum(1 for _ in turbo_dir.glob("schema_*.json") if _.is_file())
    except Exception as exc:
        logger.debug(
            "[schema_loader] schema_*.json count failed for %s: %s",
            turbo_dir,
            exc,
        )

    return TurboFace(
        turbo_name=turbo_name,
        source_skill=fm.get("source_skill", skill_dir.name),
        turbo_dir=str(turbo_dir.resolve()),
        description=fm.get("description", ""),
        scenario_count=scenario_count,
    )


def _discover_all_turbo_faces_uncached(skill_roots: list[str] | None = None) -> list[TurboFace]:
    """实际扫描逻辑（无缓存）：收集每个 root 下全部 turbo 面."""
    roots = [Path(r) for r in skill_roots] if skill_roots else _iter_candidate_skill_roots()
    faces: list[TurboFace] = []
    seen_dirs: set[str] = set()
    for root in roots:
        for skill_dir in _iter_turbo_skill_dirs(root):
            try:
                key = str(skill_dir.resolve())
            except Exception:
                key = str(skill_dir)
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            face = _build_turbo_face(skill_dir)
            if face is not None:
                faces.append(face)
    return faces


def _iter_candidate_skill_roots() -> list[Path]:
    """获取候选 skill 根目录列表，与 SkillUseRail / DeepAdapter 对齐.

    优先级：
    1. ContextVar（SkillTurboRail / adapter 注入的 ``_resolve_skill_dirs()`` 结果）
    2. ``resolve_agent_registered_skill_dirs()``（共享 tip dirs → workspace/skills）
    """
    roots: list[Path] = []
    try:
        from jiuwenswarm.agents.skill_turbo.online.context_vars import (
            get_skill_turbo_skill_roots,
        )

        injected = get_skill_turbo_skill_roots()
        if injected:
            roots = [Path(p) for p in injected if str(p).strip()]
    except Exception as exc:
        logger.debug(
            "[schema_loader] read skill_turbo_skill_roots ContextVar failed: %s", exc
        )

    if not roots:
        try:
            from jiuwenswarm.common.utils import resolve_agent_registered_skill_dirs

            roots = list(resolve_agent_registered_skill_dirs())
        except Exception as exc:
            logger.warning(
                "[schema_loader] resolve_agent_registered_skill_dirs EXCEPTION: %s",
                exc,
                exc_info=True,
            )
            return []

    logger.info(
        "[schema_loader] _iter_candidate_skill_roots returned %d roots: %s",
        len(roots),
        [str(r) for r in roots],
    )
    return roots


def discover_turbo_face(skill_root: str) -> TurboFace | None:
    """发现指定 skill_root 下的第一个 turbo 面.

    Args:
        skill_root: skill 根目录路径（包含多个 skill 子目录的目录，
                     或直接包含 turbo/ 的 skill 目录）

    Returns:
        TurboFace 或 None（未找到）。多 skill 场景请用 ``discover_all_turbo_faces``。
    """
    for skill_dir in _iter_turbo_skill_dirs(Path(skill_root)):
        face = _build_turbo_face(skill_dir)
        if face is not None:
            return face
    return None


def discover_all_turbo_faces(skill_roots: list[str] | None = None) -> list[TurboFace]:
    """发现所有已注册 skill 下的 turbo 面（进程级缓存 + TTL/mtime 失效）.

    Args:
        skill_roots: 候选 skill 根目录列表；None 时自动发现

    Returns:
        TurboFace 列表
    """
    # 规范化 roots（用于缓存 key）
    if skill_roots is None:
        roots = _iter_candidate_skill_roots()
    else:
        roots = [Path(r) for r in skill_roots]
    
    cache_key = frozenset(str(r.resolve()) for r in roots)
    now = time.time()
    
    # 检查缓存
    cached = _TURBO_FACES_CACHE.get(cache_key)
    if cached is not None:
        cached_at, mtime_snapshot, faces = cached
        # TTL 检查
        if now - cached_at < _TURBO_FACES_CACHE_TTL:
            # mtime 检查：任一 SKILL_TURBO.md 变化则失效
            current_mtime = _get_mtime_snapshot(roots)
            if current_mtime == mtime_snapshot:
                logger.debug(
                    "[schema_loader] turbo faces cache hit: %d faces, age=%.1fs",
                    len(faces), now - cached_at
                )
                return faces
            else:
                logger.info(
                    "[schema_loader] turbo faces cache invalidated by mtime change"
                )
        else:
            logger.info(
                "[schema_loader] turbo faces cache expired: age=%.1fs > TTL=%.1fs",
                now - cached_at, _TURBO_FACES_CACHE_TTL
            )
    
    # 缓存未命中或失效，重新扫描（显式传入已解析 roots，避免二次解析分叉）
    root_strs = [str(r) for r in roots]
    faces = _discover_all_turbo_faces_uncached(root_strs)
    mtime_snapshot = _get_mtime_snapshot(roots)
    _TURBO_FACES_CACHE[cache_key] = (now, mtime_snapshot, faces)
    logger.info(
        "[schema_loader] turbo faces cached: %d faces from %d roots: %s",
        len(faces),
        len(roots),
        root_strs,
    )
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
        ValueError: scenario 格式非法（含路径穿越）
    """
    scenario = validate_scenario(scenario)
    schema_path = Path(turbo_dir) / f"schema_{scenario}.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"schema not found: {schema_path}")
    text = schema_path.read_text(encoding="utf-8")
    return json.loads(text)


def extract_scenario_summaries(turbo_dir: str) -> list[dict[str, str]]:
    """从所有 schema_*.json 提取场景摘要（scenario_id + 触发条件）.

    从 schema 文件读取元数据，schema 顶层有 scenario_id（如 "create_ppt"）和
    task_description（如"根据用户主题...生成 PPT"），后者充当触发条件供 discover
    模式返回给 Agent 据触发条件选择正确场景。

    Returns:
        场景摘要列表，每项含 scenario_id 和 trigger（从 task_description 映射）
    """
    turbo_path = Path(turbo_dir)
    if not turbo_path.exists():
        return []

    summaries: list[dict[str, str]] = []
    for schema_file in turbo_path.glob("schema_*.json"):
        try:
            text = schema_file.read_text(encoding="utf-8")
            schema = json.loads(text)
            scenario_id = schema.get("scenario_id", "")
            task_description = schema.get("task_description", "")
            if scenario_id and task_description:
                summaries.append({
                    "scenario_id": scenario_id,
                    "trigger": task_description,  # task_description 充当触发条件
                })
        except Exception as exc:
            logger.debug(
                "[schema_loader] extract scenario from %s failed: %s",
                schema_file.name, exc,
            )
            continue

    return summaries


def extract_scenario_selection_rules(turbo_dir: str) -> str:
    """从 SKILL_TURBO.md 提取场景选择规则段（含规则表 + 反模式禁令）.

    提取 '## 场景清单' 到第一个 '### 场景一' 之间的文本，
    包含场景选择规则表和反模式禁令，供 discover 模式返回给 Agent。
    """
    md_path = Path(turbo_dir) / "SKILL_TURBO.md"
    if not md_path.exists():
        return ""
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return ""

    fm_match = _FRONTMATTER_RE.match(text)
    if fm_match:
        text = text[fm_match.end():]

    # Find "## 场景清单" section
    idx_start = text.find("## 场景清单")
    if idx_start < 0:
        return ""
    # Find first "### 场景一" after it
    idx_end = text.find("### 场景一", idx_start)
    if idx_end < 0:
        # fallback: find any "### 场景" header
        idx_end = text.find("### 场景", idx_start + len("## 场景清单"))
    if idx_end < 0:
        return text[idx_start:]
    return text[idx_start:idx_end].strip()


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
    flow = schema.get("execution_flow", []) or []
    parts: list[str] = []
    seen: set[str] = set()
    for step in flow:
        if not isinstance(step, dict):
            continue
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
