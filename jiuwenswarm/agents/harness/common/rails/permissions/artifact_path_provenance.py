# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Session artifact-path provenance backed by a lightweight Host post gate."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from jiuwenswarm.agents.harness.common.rails.permissions.artifact_path_post_gate import (
    MAX_ARTIFACT_CANDIDATES,
    ArtifactCandidateState,
    ArtifactPostGateResult,
    _canonical_regular_file,
    _is_protected,
    _path_is_grounded,
    _protected_roots,
    _safe_effective_workdir,
    _safe_workspace_root,
    _validate_candidate,
    clear_artifact_candidate_state,
    collect_grounding_texts,
    consume_artifact_candidate_state,
    publish_artifact_candidate_state,
    stage_semantic_artifact_paths,
    tool_result_succeeded,
)

MAX_SESSION_ARTIFACT_PATHS = 256


@dataclass(frozen=True, slots=True)
class ArtifactProvenanceEntry:
    """Session-local metadata for one verified artifact path."""

    canonical_path: str
    source_tool: str
    source_tool_call_id: str
    recorded_at: float


class SessionArtifactPathProvenance:
    """Keep exact, verified file paths for one root-session runtime."""

    def __init__(self, root_session_id: str = "") -> None:
        self.root_session_id = str(root_session_id or "").strip()
        self._entries: OrderedDict[str, ArtifactProvenanceEntry] = OrderedDict()
        self._lock = RLock()
        self._closed = False

    def bind_session(self, root_session_id: str) -> None:
        session_id = str(root_session_id or "").strip()
        if not session_id:
            raise ValueError("artifact_provenance_session_missing")
        with self._lock:
            if self._closed:
                raise ValueError("artifact_provenance_session_closed")
            if self.root_session_id and self.root_session_id != session_id:
                raise ValueError("artifact_provenance_session_mismatch")
            self.root_session_id = session_id

    def record_verified(
        self,
        *,
        state: ArtifactCandidateState,
        excluded_paths: Sequence[str] = (),
    ) -> ArtifactPostGateResult:
        """Validate successful-call candidates and add exact regular files."""

        with self._lock:
            if (
                self._closed
                or not self.root_session_id
                or state.session_id != self.root_session_id
            ):
                return ArtifactPostGateResult(
                    rejected=len(state.candidates),
                    reason_codes=("session_mismatch",),
                )

        root = _safe_workspace_root(state.workspace_root)
        if root is None:
            return ArtifactPostGateResult(
                rejected=len(state.candidates),
                reason_codes=("workspace_unavailable",),
            )
        protected_roots = _protected_roots(root, excluded_paths)
        effective_workdir = _safe_effective_workdir(
            root,
            state.effective_workdir,
        )
        accepted_entries: list[ArtifactProvenanceEntry] = []
        reason_codes: list[str] = []
        rejected = 0
        seen: set[str] = set()
        for candidate in state.candidates[:MAX_ARTIFACT_CANDIDATES]:
            resolved, reason = _validate_candidate(
                candidate,
                root=root,
                protected_roots=protected_roots,
                grounding_texts=state.grounding_texts,
                effective_workdir=effective_workdir,
            )
            if resolved is None:
                rejected += 1
                if reason and reason not in reason_codes:
                    reason_codes.append(reason)
                continue
            canonical = resolved.as_posix()
            if canonical in seen:
                continue
            seen.add(canonical)
            accepted_entries.append(
                ArtifactProvenanceEntry(
                    canonical_path=canonical,
                    source_tool=state.tool_name,
                    source_tool_call_id=state.tool_call_id,
                    recorded_at=time.time(),
                )
            )

        with self._lock:
            if self._closed or state.session_id != self.root_session_id:
                return ArtifactPostGateResult(
                    rejected=len(state.candidates),
                    reason_codes=("session_mismatch",),
                )
            for entry in accepted_entries:
                self._entries.pop(entry.canonical_path, None)
                self._entries[entry.canonical_path] = entry
                while len(self._entries) > MAX_SESSION_ARTIFACT_PATHS:
                    self._entries.popitem(last=False)
        return ArtifactPostGateResult(
            accepted=len(accepted_entries),
            rejected=rejected,
            reason_codes=tuple(reason_codes),
        )

    def relevant_paths(
        self,
        *,
        root_session_id: str,
        workspace_root: str | Path | None,
        access_paths: Iterable[str] = (),
        grounding_texts: Iterable[str] = (),
        effective_workdir: str | Path | None = None,
        excluded_paths: Sequence[str] = (),
        limit: int = 8,
    ) -> tuple[str, ...]:
        """Return existing provenance paths referenced by the current call."""

        root = _safe_workspace_root(workspace_root)
        normalized_limit = max(int(limit), 0)
        if root is None or normalized_limit == 0:
            return ()
        with self._lock:
            if (
                self._closed
                or not root_session_id
                or root_session_id != self.root_session_id
            ):
                return ()
            entries = tuple(self._entries.values())

        protected_roots = _protected_roots(root, excluded_paths)
        grounded_workdir = _safe_effective_workdir(root, effective_workdir)
        canonical_accesses = {
            path.as_posix()
            for raw_path in access_paths
            if (path := _canonical_regular_file(raw_path, root=root)) is not None
        }
        texts = tuple(str(value) for value in grounding_texts if str(value))
        relevant: list[str] = []
        stale: list[str] = []
        for entry in entries:
            path = _canonical_regular_file(entry.canonical_path, root=root)
            if path is None or _is_protected(path, protected_roots):
                stale.append(entry.canonical_path)
                continue
            canonical = path.as_posix()
            relative = path.relative_to(root).as_posix()
            if canonical not in canonical_accesses and not _path_is_grounded(
                relative,
                root=root,
                texts=texts,
                effective_workdir=grounded_workdir,
            ):
                continue
            relevant.append(relative)
            if len(relevant) >= normalized_limit:
                break
        if stale:
            with self._lock:
                for canonical in stale:
                    self._entries.pop(canonical, None)
        return tuple(relevant)

    def contains(self, *, root_session_id: str, path: str | Path) -> bool:
        try:
            canonical = Path(path).expanduser().resolve(strict=True).as_posix()
        except (OSError, RuntimeError, TypeError, ValueError):
            return False
        with self._lock:
            return bool(
                not self._closed
                and root_session_id == self.root_session_id
                and canonical in self._entries
            )

    def dispose(self) -> None:
        with self._lock:
            self._entries.clear()
            self._closed = True

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


__all__ = [
    "ArtifactCandidateState",
    "ArtifactPostGateResult",
    "MAX_SESSION_ARTIFACT_PATHS",
    "SessionArtifactPathProvenance",
    "clear_artifact_candidate_state",
    "collect_grounding_texts",
    "consume_artifact_candidate_state",
    "publish_artifact_candidate_state",
    "stage_semantic_artifact_paths",
    "tool_result_succeeded",
]
