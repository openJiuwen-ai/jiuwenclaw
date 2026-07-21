"""Fail-closed retention and reconciliation of uncertain provider ownership."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import PROCESS_TERMINATE_GRACE_SECONDS
from .errors import CodexProviderError
from .locking import release_profile_lock
from .process_lifecycle import (
    current_boot_identity,
    process_group_is_empty,
    process_group_snapshot,
    terminate_process_group,
)
from .profiles import CodexProfile
from .turn_directory import cleanup_owned_turn_directory


@dataclass
class _QuarantinedOwnership:
    profile: CodexProfile
    process: asyncio.subprocess.Process | None
    pgid: int | None
    boot_id: str | None
    members: tuple[tuple[int, int], ...]
    lock_handle: Any | None = None
    turn_dir: Path | None = None


_QUARANTINES: dict[Path, _QuarantinedOwnership] = {}


def _key(profile: CodexProfile) -> Path:
    return profile.root.absolute()


def _safe_error() -> CodexProviderError:
    return CodexProviderError(
        "provider_quarantined",
        "Codex is unavailable until uncertain process ownership is safely reconciled.",
    )


def _member_identities(pgid: int | None) -> tuple[tuple[int, int], ...]:
    return tuple(
        (int(member["pid"]), int(member["start_ticks"]))
        for member in process_group_snapshot(pgid)
    )


def _marker_payload(record: _QuarantinedOwnership) -> bytes:
    payload = {
        "version": 1,
        "boot_id": record.boot_id,
        "pgid": record.pgid,
        "members": [[pid, start_ticks] for pid, start_ticks in record.members],
        "turn_name": record.turn_dir.name if record.turn_dir is not None else None,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _validate_marker_file(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise _safe_error()
    if os.name == "posix" and (
        info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise _safe_error()


def _write_marker(record: _QuarantinedOwnership) -> None:
    path = record.profile.quarantine_path
    if path.exists() or path.is_symlink():
        _validate_marker_file(path)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        payload = _marker_payload(record)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _read_marker(profile: CodexProfile) -> dict[str, Any] | None:
    path = profile.quarantine_path
    if not path.exists() and not path.is_symlink():
        return None
    _validate_marker_file(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if info.st_size > 16 * 1024:
            raise _safe_error()
        payload = os.read(descriptor, 16 * 1024 + 1)
    finally:
        os.close(descriptor)
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _safe_error() from None
    if not isinstance(decoded, dict) or decoded.get("version") != 1:
        raise _safe_error()
    return decoded


def _remove_marker(profile: CodexProfile) -> None:
    path = profile.quarantine_path
    if not path.exists() and not path.is_symlink():
        return
    _validate_marker_file(path)
    path.unlink()
    directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def quarantine_ownership(
    profile: CodexProfile,
    *,
    process: asyncio.subprocess.Process | None,
    pgid: int | None,
    lock_handle: Any | None = None,
    turn_dir: Path | None = None,
) -> None:
    """Persist non-secret ownership identity and retain live resource handles."""

    key = _key(profile)
    existing = _QUARANTINES.get(key)
    if existing is not None:
        if existing.process is not process and process is not None:
            raise _safe_error()
        if lock_handle is not None:
            existing.lock_handle = lock_handle
        if turn_dir is not None:
            existing.turn_dir = turn_dir
        _write_marker(existing)
        return
    record = _QuarantinedOwnership(
        profile=profile,
        process=process,
        pgid=pgid,
        boot_id=current_boot_identity(),
        members=_member_identities(pgid),
        lock_handle=lock_handle,
        turn_dir=turn_dir,
    )
    _QUARANTINES[key] = record
    _write_marker(record)


def profile_is_quarantined(profile: CodexProfile) -> bool:
    return _key(profile) in _QUARANTINES or profile.quarantine_path.exists() or profile.quarantine_path.is_symlink()


def _marker_identity(marker: dict[str, Any]) -> tuple[str | None, int | None, set[tuple[int, int]], str | None]:
    boot_id = marker.get("boot_id")
    pgid = marker.get("pgid")
    members = marker.get("members")
    turn_name = marker.get("turn_name")
    if boot_id is not None and not isinstance(boot_id, str):
        raise _safe_error()
    if pgid is not None and (not isinstance(pgid, int) or pgid <= 0):
        raise _safe_error()
    if turn_name is not None and (
        not isinstance(turn_name, str)
        or not turn_name.startswith("turn-")
        or Path(turn_name).name != turn_name
    ):
        raise _safe_error()
    if not isinstance(members, list):
        raise _safe_error()
    identities: set[tuple[int, int]] = set()
    for item in members:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(value, int) and value >= 0 for value in item)
        ):
            raise _safe_error()
        identities.add((item[0], item[1]))
    return boot_id, pgid, identities, turn_name


def _identity_is_unambiguous(
    boot_id: str | None,
    pgid: int | None,
    identities: set[tuple[int, int]],
) -> bool:
    if boot_id is None or boot_id != current_boot_identity():
        return False
    current = {
        (int(member["pid"]), int(member["start_ticks"]))
        for member in process_group_snapshot(pgid)
    }
    return not current or (bool(identities) and current <= identities)


async def reconcile_profile_quarantine(profile: CodexProfile) -> None:
    """Clear quarantine only after identity-safe, full ownership reconciliation."""

    marker = _read_marker(profile)
    record = _QUARANTINES.get(_key(profile))
    if marker is None and record is None:
        return
    if marker is None or record is None:
        # A marker without an in-memory process owner may be cleared only when
        # its recorded group is empty; it is never used to signal a stale PGID.
        if marker is None:
            raise _safe_error()
        boot_id, pgid, _identities, turn_name = _marker_identity(marker)
        if boot_id != current_boot_identity() or not process_group_is_empty(pgid):
            raise _safe_error()
        if turn_name is not None:
            cleanup_owned_turn_directory(profile.turns_dir / turn_name, profile.turns_dir)
        _remove_marker(profile)
        return

    boot_id, pgid, identities, _turn_name = _marker_identity(marker)
    if pgid != record.pgid or boot_id != record.boot_id:
        raise _safe_error()
    if not _identity_is_unambiguous(boot_id, pgid, identities):
        raise _safe_error()
    if record.process is not None:
        try:
            await terminate_process_group(
                record.process,
                record.pgid,
                lambda _event, **_fields: None,
                grace_seconds=PROCESS_TERMINATE_GRACE_SECONDS,
            )
        except BaseException:
            raise _safe_error() from None
    elif not process_group_is_empty(record.pgid):
        raise _safe_error()
    if record.turn_dir is not None:
        try:
            cleanup_owned_turn_directory(record.turn_dir, profile.turns_dir)
        except BaseException:
            raise _safe_error() from None
    if record.lock_handle is not None:
        try:
            release_profile_lock(record.lock_handle)
        except BaseException:
            raise _safe_error() from None
    _remove_marker(profile)
    _QUARANTINES.pop(_key(profile), None)


def reset_quarantines_for_tests() -> None:
    """Drop only in-memory test state; callers must already own cleanup."""

    _QUARANTINES.clear()
