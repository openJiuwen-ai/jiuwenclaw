# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-scoped final-deliverables and per-member work directories.

A team member without a project directory still needs stable places: a
*per-member* temporary working directory (cwd, where intermediate files land
in isolation) and a *shared* final-deliverables directory (where the whole
team's outputs collect, distinguished by filename). Single-agent projectless
runs use ``<Documents>/JiuwenSwarm/<YYYY-MM-DD>/chat-<n>/`` (see
:mod:`jiuwenswarm.common.projectless_workspace`); teams cannot reuse that,
because the single-agent root is per-session and lives outside the team
workspace's git repository (so auto-commit / history would not apply).

Instead the team's artifacts live inside the team workspace itself:

    <team-workspace>/artifacts/<YYYY-MM-DD>/chat-<n>/
        work/<member_slug>/      # per-member temporary working directory (cwd)
        outputs/                 # shared final deliverables (by filename)

All members of one team share one ``chat-<n>`` directory. ``outputs/`` is
shared (one directory, filename-distinguished); ``work/`` is per-member so
each member's intermediate files, caches and scratch scripts are isolated and
do not pile up together — a member's cwd is its own ``work/<member_slug>/``,
not the shared member workspace (which mixes internal data with scratch).

The tree lives under ``team-workspace`` deliberately: it is inside the team
workspace's git repository, so ``TeamWorkspaceRail`` auto-commit and the
``workspace_meta`` tool's git history work on deliverables without extra
plumbing. The ``.session_id`` marker and ``metadata.json`` record adopt the
same on-disk shape as the single-agent allocator, so a future unification can
fold both into one implementation without touching the layout.
"""

from __future__ import annotations

import datetime as _datetime
import json
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path


_ARTIFACTS_SUBDIR = "artifacts"
_CHAT_DIR_PREFIX = "chat"
_METADATA_FILENAME = "metadata.json"
_SESSION_MARKER_FILENAME = ".session_id"
_WORK_SUBDIR = "work"
_MAX_SESSION_SLUG_LENGTH = 48
_MAX_MEMBER_SLUG_LENGTH = 48
_WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CLOCK$",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "CON",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
    "NUL",
    "PRN",
}


@dataclass(frozen=True, slots=True)
class TeamArtifactWorkspace:
    """Shared root and deliverables directory exposed to one team session.

    Both fields are per-team (shared by every member). A member's own temporary
    working directory is per-member and resolved separately via
    :func:`resolve_member_work_dir`, because the allocator runs before the
    per-member view exists.
    """

    root_dir: Path
    outputs_dir: Path


def get_team_artifacts_dir(team_ws_root: str | os.PathLike[str]) -> Path:
    """Return the artifacts root of a team workspace.

    The artifacts root is always ``<team-workspace>/artifacts``. Allocation of
    dated ``chat-<n>`` sub-directories underneath happens in
    :func:`get_team_artifact_workspace`.
    """
    return (Path(team_ws_root) / _ARTIFACTS_SUBDIR).resolve()


def get_team_artifact_workspace(
    team_ws_root: str | os.PathLike[str],
    *,
    session_id: str | None = None,
    task_name: str | None = None,
) -> TeamArtifactWorkspace:
    """Create or reuse the shared deliverables directory for a team session.

    Reuses the same ``chat-<n>`` directory for the duration of a team session
    (all members share one), keyed by the team session id. New directories use
    ASCII-only ``chat-<n>`` names; the original query/title is stored in
    ``metadata.json`` instead of the path, exactly as the single-agent
    allocator does.

    Args:
        team_ws_root: The team workspace root path.
        session_id: The team session id (shared by every member of the team).
        task_name: Optional human-readable task/query title persisted to
            ``metadata.json`` (never used in the path).

    Returns:
        A :class:`TeamArtifactWorkspace` with the shared root and the created
        ``outputs/`` directory. A member's own ``work/<member_slug>/`` is not
        created here — call :func:`resolve_member_work_dir` per member.
    """
    artifacts_dir = get_team_artifacts_dir(team_ws_root)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    safe_session = _slugify_session(session_id)
    root = _resolve_registered_root(artifacts_dir, safe_session)
    if root is None:
        task_date = _datetime.datetime.now().astimezone().strftime("%Y-%m-%d")
        root = _allocate_task_root(artifacts_dir, task_date, safe_session)
        _write_registered_root(artifacts_dir, safe_session, root)
        _write_task_metadata(root, session_id=session_id, task_name=task_name)
    elif not (root / _METADATA_FILENAME).exists():
        _write_task_metadata(root, session_id=session_id, task_name=task_name)

    outputs_dir = root / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    return TeamArtifactWorkspace(
        root_dir=root,
        outputs_dir=outputs_dir,
    )


def resolve_member_work_dir(
    root_dir: str | os.PathLike[str],
    member_name: str | None,
) -> Path:
    """Create or reuse a member's isolated temporary working directory.

    Each member of a projectless team runs in its own ``work/<member_slug>/``
    under the shared artifact root, so intermediate files, caches and scratch
    scripts stay isolated instead of piling up together. The directory is
    created on first call; later calls reuse it.

    Args:
        root_dir: The shared artifact root (``TeamArtifactWorkspace.root_dir``).
        member_name: The member's semantic name. Falls back to ``"default"``
            when empty, so a nameless call still gets a real directory.

    Returns:
        The created/reused per-member working directory.
    """
    safe_member = _slugify_member(member_name)
    work_dir = Path(root_dir) / _WORK_SUBDIR / safe_member
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _slugify_session(value: str | None) -> str:
    """Reduce a session id to a stable, filesystem-safe allocation key.

    The marker file stores the full session id; this slug is only used for the
    registry file name and the allocation-time collision probe.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"[^\w.-]+", "-", text, flags=re.UNICODE)
    text = text.strip(" .-_")
    text = text[:_MAX_SESSION_SLUG_LENGTH].rstrip(" .-_")
    if not text:
        return "default"
    if text.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        return f"session-{text}"
    return text


def _slugify_member(value: str | None) -> str:
    """Reduce a member name to a stable, filesystem-safe directory name."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    text = re.sub(r"[^\w.-]+", "-", text, flags=re.UNICODE)
    text = text.strip(" .-_")
    text = text[:_MAX_MEMBER_SLUG_LENGTH].rstrip(" .-_")
    if not text:
        return "default"
    if text.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        return f"member-{text}"
    return text


def _allocate_task_root(
    artifacts_dir: Path,
    task_date: str,
    safe_session: str,
) -> Path:
    """Pick the next free ``chat-<n>`` directory for a dated session.

    A directory owned by the same session id is reused; otherwise the next free
    index is claimed. Mirrors the single-agent allocator's allocation loop.
    """
    date_dir = artifacts_dir / task_date
    date_dir.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        candidate = date_dir / f"{_CHAT_DIR_PREFIX}-{index}"
        if candidate.exists():
            if _read_session_marker(candidate) == safe_session:
                return candidate.resolve()
            index += 1
            continue
        try:
            candidate.mkdir(parents=True)
        except FileExistsError:
            continue
        _write_session_marker(candidate, safe_session)
        return candidate.resolve()


def _resolve_registered_root(
    artifacts_dir: Path,
    safe_session: str,
) -> Path | None:
    """Look up an already-allocated root for this session in the registry.

    The registry lives next to the artifacts it tracks (``.team_artifacts``
    under the artifacts root), so a relocated team workspace stays
    self-contained. A registered root that no longer exists on disk (for
    example a workspace that was wiped) is ignored so a fresh one is allocated.
    """
    registry_path = _registry_path(artifacts_dir, safe_session)
    if not registry_path.exists():
        return None
    try:
        record = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    raw_root = record.get("root_dir") if isinstance(record, dict) else None
    if not isinstance(raw_root, str) or not raw_root.strip():
        return None
    root = Path(raw_root).expanduser().resolve()
    if not root.is_relative_to(artifacts_dir.resolve()) or not root.is_dir():
        return None
    return root


def _registry_path(artifacts_dir: Path, safe_session: str) -> Path:
    return artifacts_dir / ".team_artifacts" / f"{safe_session}.json"


def _write_registered_root(
    artifacts_dir: Path,
    safe_session: str,
    root: Path,
) -> None:
    """Persist the session -> root binding so a later turn reuses it."""
    registry_dir = artifacts_dir / ".team_artifacts"
    registry_dir.mkdir(parents=True, exist_ok=True)
    path = _registry_path(artifacts_dir, safe_session)
    # A unique sibling keeps concurrent turns for the same session from
    # clobbering one another's temporary registry file before os.replace().
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps({"root_dir": str(root)}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_task_metadata(
    root: Path,
    *,
    session_id: str | None,
    task_name: str | None,
) -> None:
    """Persist query/title separately from the ASCII-only workspace name."""
    metadata_path = root / _METADATA_FILENAME
    now = _datetime.datetime.now().astimezone().isoformat()
    query = str(task_name or "")
    metadata = {
        "chat_id": root.name,
        "session_id": str(session_id or ""),
        "title": query,
        "query": query,
        "created_at": now,
    }
    temporary = metadata_path.with_name(
        f".{metadata_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, metadata_path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_session_marker(root: Path) -> str | None:
    try:
        marker = (root / _SESSION_MARKER_FILENAME).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return marker or None


def _write_session_marker(root: Path, safe_session: str) -> None:
    try:
        (root / _SESSION_MARKER_FILENAME).write_text(safe_session, encoding="utf-8")
    except OSError:
        # The registry remains the source of truth. A marker is only needed
        # to disambiguate a title collision during allocation.
        pass


__all__ = [
    "TeamArtifactWorkspace",
    "get_team_artifacts_dir",
    "get_team_artifact_workspace",
    "resolve_member_work_dir",
]
