# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session-scoped Leader skill mounting for team-mode prompt selections."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from jiuwenclaw.agentserver.session_metadata import (
    _enqueue_write,
    get_session_metadata,
)
from jiuwenclaw.agentserver.team.team_skill_links import (
    is_valid_skill_dir,
    link_skill_dir,
    path_exists_or_link,
)
from jiuwenclaw.utils import get_agent_skills_dir, get_shared_agent_skills_dirs

logger = logging.getLogger(__name__)

_SESSION_SKILLS_METADATA_KEY = "team_leader_prompt_skills"
_MAX_SKILL_NAME_LENGTH = 128
_MAX_SESSION_SKILLS = 32
_PROMPT_SKILL_RE = re.compile(
    r"使用\s+([^\r\n，。；;!?！？]{1,128}?)\s+技能(?=\s|[，。；;!?！？]|$)"
)


@dataclass(frozen=True)
class PromptSkillMountResult:
    """Outcome of one best-effort prompt-skill synchronization."""

    selected_names: tuple[str, ...]
    mounted_names: tuple[str, ...]
    missing_names: tuple[str, ...]


def _is_safe_skill_name(name: str) -> bool:
    return bool(
        name
        and len(name) <= _MAX_SKILL_NAME_LENGTH
        and name not in {".", ".."}
        and "\x00" not in name
        and "/" not in name
        and "\\" not in name
    )


def _deduplicate_names(names: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_name in names:
        name = str(raw_name or "").strip()
        if not _is_safe_skill_name(name):
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
        if len(result) >= _MAX_SESSION_SKILLS:
            break
    return result


def extract_prompt_skill_names(query: str) -> list[str]:
    """Extract ordered skill names from prompt skill selection phrases."""
    if not isinstance(query, str) or not query:
        return []
    return _deduplicate_names(match.group(1) for match in _PROMPT_SKILL_RE.finditer(query))


def resolve_prompt_skill_roots() -> list[Path]:
    """Return official roots first, followed by shared/user-installed roots."""
    shared_roots = get_shared_agent_skills_dirs()
    official_roots = [root for root in shared_roots if (root / "BOOTSTRAP.md").is_file()]
    remaining_roots = [root for root in shared_roots if root not in official_roots]

    ordered: list[Path] = []
    seen: set[str] = set()
    for root in (*official_roots, *remaining_roots, get_agent_skills_dir()):
        try:
            resolved = root.expanduser().resolve()
        except (OSError, ValueError):
            continue
        key = str(resolved).casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(resolved)
    return ordered


def _skill_index(roots: Iterable[Path]) -> dict[str, Path]:
    """Index valid skill directories; the first root wins on name collision."""
    index: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        try:
            children = list(root.iterdir())
        except OSError as exc:
            logger.warning("[PromptSkillMount] failed to scan skill root=%s error=%s", root, exc)
            continue
        for child in children:
            if not is_valid_skill_dir(child):
                continue
            index.setdefault(child.name.casefold(), child)
    return index


def _read_selected_names(session_id: str) -> list[str]:
    metadata = get_session_metadata(session_id)
    raw = metadata.get(_SESSION_SKILLS_METADATA_KEY, [])
    if not isinstance(raw, list):
        return []
    return _deduplicate_names(str(item) for item in raw)


def _persist_selected_names(session_id: str, names: list[str]) -> None:
    metadata = get_session_metadata(session_id)
    metadata[_SESSION_SKILLS_METADATA_KEY] = list(names)
    _enqueue_write(session_id, metadata)


def mount_leader_prompt_skills(
    *,
    session_id: str,
    query: str,
    target_dir: Path,
    skill_roots: Iterable[Path] | None = None,
) -> PromptSkillMountResult:
    """Best-effort mount prompt-selected skills into one Leader session view.

    Selections accumulate in session metadata. Missing or unmountable skills are
    logged and returned to the caller, but never abort the user request.
    """
    previous = _read_selected_names(session_id)
    requested = extract_prompt_skill_names(query)
    selected = _deduplicate_names((*previous, *requested))
    if selected != previous:
        _persist_selected_names(session_id, selected)

    if not selected:
        return PromptSkillMountResult((), (), ())

    roots = list(skill_roots) if skill_roots is not None else resolve_prompt_skill_roots()
    index = _skill_index(roots)
    mounted: list[str] = []
    missing: list[str] = []
    target_dir.mkdir(parents=True, exist_ok=True)

    for selected_name in selected:
        source = index.get(selected_name.casefold())
        if source is None:
            missing.append(selected_name)
            continue
        resolved_name = source.name
        destination = target_dir / resolved_name
        try:
            if not path_exists_or_link(destination):
                link_skill_dir(source, destination)
            mounted.append(resolved_name)
        except Exception as exc:  # best-effort by product requirement
            missing.append(selected_name)
            logger.warning(
                "[PromptSkillMount] failed to mount skill=%s source=%s target=%s error=%s",
                selected_name,
                source,
                destination,
                exc,
            )

    logger.info(
        "[PromptSkillMount] synchronized Leader skills: session_id=%s selected=%s mounted=%s missing=%s roots=%s",
        session_id,
        selected,
        mounted,
        missing,
        [str(root) for root in roots],
    )
    return PromptSkillMountResult(tuple(selected), tuple(mounted), tuple(missing))


__all__ = [
    "PromptSkillMountResult",
    "extract_prompt_skill_names",
    "mount_leader_prompt_skills",
    "resolve_prompt_skill_roots",
]
