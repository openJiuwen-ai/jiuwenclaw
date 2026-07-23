"""Strict cross-turn quarantine for the Claude runtime, keyed on process groups.

When a turn cannot confirm its child process group was fully reaped, the runner
records that group's id and fails closed. Every subsequent turn reconciles at
start: it permits a new turn ONLY when every recorded group is proven gone;
otherwise it blocks (fail closed) until an operator resolves the lingering
process.

Durability + fail-closed: the authoritative record is an **in-process set** that
cannot fail to persist for the life of the server process, so a marker-file write
error can never let a later turn run past an unreaped group (fail closed, never
fail open). The on-disk marker is a best-effort mirror that additionally survives
a process restart. All record/reconcile operations are serialized by a lock so
concurrent updates cannot lose records.

Why check-only (no active kill during reconcile): a recorded pgid could later be
reused by an unrelated process. Killing on reuse would harm an innocent process,
so reconcile only *checks* liveness. The reuse ambiguity errs safe - a reused
(hence "alive") pgid keeps us blocked (over-blocks, never under-blocks), and a
group that is genuinely gone raises ``ProcessLookupError`` and clears cleanly.

The marker holds only integer process-group ids - never any credential or
account material.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Iterable

from .errors import ClaudeProviderError, claude_provider_unavailable

_MARKER_NAME = "uncertain-process-groups.json"

# Authoritative in-process record (cannot fail to persist within this process),
# and a lock serializing all record/reconcile work so concurrent updates never
# lose a group.
_IN_PROCESS_GROUPS: set[int] = set()
_LOCK = threading.Lock()


def _marker_path(root: Path) -> Path:
    return root / _MARKER_NAME


def _read_recorded_groups(path: Path) -> list[int]:
    """Return recorded pgids from the marker, or raise fail-closed on tampering."""
    if path.is_symlink():
        raise ClaudeProviderError(
            "provider_unavailable", "The Claude quarantine marker is not a regular file."
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise claude_provider_unavailable() from exc
    try:
        document = json.loads(raw)
        groups = document["process_groups"]
        if not isinstance(groups, list) or not all(
            isinstance(value, int) and not isinstance(value, bool) for value in groups
        ):
            raise ValueError("malformed process_groups")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        # A marker we cannot trust means we cannot prove safety: fail closed.
        raise claude_provider_unavailable() from exc
    return groups


def _write_marker(root: Path, groups: set[int]) -> None:
    """Best-effort atomic write of the marker (mode 0o600). Failure is tolerated
    because the in-process set is the authoritative fail-closed record."""
    path = _marker_path(root)
    payload = json.dumps({"process_groups": sorted(groups)}).encode("utf-8")
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".uncertain-", dir=str(root))
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, payload)
        finally:
            os.close(fd)
        os.replace(tmp_name, path)
    except OSError:
        # Tolerated: the in-process set still blocks subsequent turns this process.
        return


def record_uncertain_groups(root: Path, pgids: Iterable[int]) -> None:
    """Record uncertain process groups (fail-closed, durable, concurrency-safe)."""
    candidates = {int(pgid) for pgid in pgids if isinstance(pgid, int) and pgid > 0}
    if not candidates:
        return
    with _LOCK:
        # Authoritative: cannot fail.
        _IN_PROCESS_GROUPS.update(candidates)
        # Best-effort mirror to disk (union of any existing file record).
        try:
            existing = set(_read_recorded_groups(_marker_path(root)))
        except ClaudeProviderError:
            existing = set()
        _write_marker(root, existing | _IN_PROCESS_GROUPS)


def reconcile_claude_quarantine(root: Path) -> None:
    """Block the turn unless every recorded uncertain process group is gone."""
    with _LOCK:
        path = _marker_path(root)
        recorded = set(_read_recorded_groups(path)) | set(_IN_PROCESS_GROUPS)
        if not recorded:
            return
        alive = {pgid for pgid in recorded if not _group_is_gone(pgid)}
        if alive:
            raise claude_provider_unavailable()
        # Every recorded group is proven gone: clear both records.
        _IN_PROCESS_GROUPS.difference_update(recorded)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise claude_provider_unavailable() from exc


def reset_quarantine_for_tests() -> None:
    """Clear the in-process record (test isolation only)."""
    with _LOCK:
        _IN_PROCESS_GROUPS.clear()


def _group_is_gone(pgid: int) -> bool:
    """True only when the process group provably no longer exists."""
    if pgid <= 0:
        # A non-positive pgid can never be safely signalled/checked; treat as
        # not-provably-gone (fail closed).
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        # The group exists but is owned by another uid: not gone.
        return False
    except OSError:
        return False
    return False
