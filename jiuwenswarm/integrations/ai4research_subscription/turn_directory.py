"""Race-resistant bounded cleanup for one provider-owned turn directory."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .constants import (
    MAX_TURN_CLEANUP_BYTES,
    MAX_TURN_CLEANUP_DEPTH,
    MAX_TURN_CLEANUP_ENTRIES,
)


class TurnDirectoryCleanupError(RuntimeError):
    """Raised when a turn directory cannot be safely removed within its bounds."""


@dataclass(frozen=True)
class _Entry:
    parent_fd: int
    name: str
    descriptor: int | None
    is_directory: bool
    identity: tuple[int, int]


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def cleanup_owned_turn_directory(turn_dir: Path, turns_dir: Path) -> None:
    """Delete through held directory FDs without following links or mount swaps."""

    if os.name != "posix":
        raise TurnDirectoryCleanupError(
            "Safe turn-directory cleanup is unavailable on this platform."
        )
    if turn_dir.parent.absolute() != turns_dir.absolute():
        raise TurnDirectoryCleanupError("Turn directory escaped its managed parent.")
    if not turn_dir.name.startswith("turn-") or Path(turn_dir.name).name != turn_dir.name:
        raise TurnDirectoryCleanupError("Turn directory name is invalid.")

    owner_fd = -1
    root_fd = -1
    opened_directories: list[int] = []
    try:
        owner_fd = os.open(turns_dir, _directory_flags(), 0o700)
        try:
            root_fd = os.open(
                turn_dir.name,
                _directory_flags(),
                0o700,
                dir_fd=owner_fd,
            )
        except FileNotFoundError:
            return
        except OSError:
            raise TurnDirectoryCleanupError(
                "Turn directory could not be opened without following links."
            ) from None

        owner_info = os.fstat(owner_fd)
        root_info = os.fstat(root_fd)
        if not stat.S_ISDIR(root_info.st_mode) or root_info.st_dev != owner_info.st_dev:
            raise TurnDirectoryCleanupError(
                "Turn directory ownership crossed a filesystem boundary."
            )
        root_identity = (root_info.st_dev, root_info.st_ino)
        entries: list[_Entry] = []
        stack: list[tuple[int, int]] = [(root_fd, 0)]
        total_bytes = 0
        while stack:
            directory_fd, depth = stack.pop()
            if depth > MAX_TURN_CLEANUP_DEPTH:
                raise TurnDirectoryCleanupError(
                    "Turn directory exceeded its cleanup depth limit."
                )
            try:
                names = os.listdir(directory_fd)
            except OSError:
                raise TurnDirectoryCleanupError(
                    "Turn directory changed during cleanup inspection."
                ) from None
            for name in names:
                if not name or name in {".", ".."} or "/" in name:
                    raise TurnDirectoryCleanupError(
                        "Turn directory contained an invalid entry."
                    )
                if len(entries) >= MAX_TURN_CLEANUP_ENTRIES:
                    raise TurnDirectoryCleanupError(
                        "Turn directory exceeded its cleanup entry limit."
                    )
                try:
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError:
                    raise TurnDirectoryCleanupError(
                        "Turn directory changed during cleanup inspection."
                    ) from None
                identity = (info.st_dev, info.st_ino)
                if stat.S_ISDIR(info.st_mode):
                    if info.st_dev != root_info.st_dev:
                        raise TurnDirectoryCleanupError(
                            "Turn directory contained a mounted filesystem."
                        )
                    try:
                        child_fd = os.open(
                            name,
                            _directory_flags(),
                            0o700,
                            dir_fd=directory_fd,
                        )
                    except OSError:
                        raise TurnDirectoryCleanupError(
                            "Turn directory changed during cleanup inspection."
                        ) from None
                    child_info = os.fstat(child_fd)
                    if (child_info.st_dev, child_info.st_ino) != identity:
                        os.close(child_fd)
                        raise TurnDirectoryCleanupError(
                            "Turn directory changed during cleanup inspection."
                        )
                    opened_directories.append(child_fd)
                    entries.append(_Entry(directory_fd, name, child_fd, True, identity))
                    stack.append((child_fd, depth + 1))
                elif stat.S_ISREG(info.st_mode):
                    total_bytes += info.st_size
                    if total_bytes > MAX_TURN_CLEANUP_BYTES:
                        raise TurnDirectoryCleanupError(
                            "Turn directory exceeded its cleanup size limit."
                        )
                    entries.append(_Entry(directory_fd, name, None, False, identity))
                elif stat.S_ISLNK(info.st_mode):
                    entries.append(_Entry(directory_fd, name, None, False, identity))
                else:
                    raise TurnDirectoryCleanupError(
                        "Turn directory contained an unsupported entry."
                    )

        current_root = os.stat(turn_dir.name, dir_fd=owner_fd, follow_symlinks=False)
        if (current_root.st_dev, current_root.st_ino) != root_identity:
            raise TurnDirectoryCleanupError("Turn directory changed during cleanup.")

        for entry in reversed(entries):
            try:
                current = os.stat(
                    entry.name,
                    dir_fd=entry.parent_fd,
                    follow_symlinks=False,
                )
            except OSError:
                raise TurnDirectoryCleanupError(
                    "Turn directory changed during cleanup."
                ) from None
            if (current.st_dev, current.st_ino) != entry.identity:
                raise TurnDirectoryCleanupError("Turn directory changed during cleanup.")
            try:
                if entry.is_directory:
                    os.rmdir(entry.name, dir_fd=entry.parent_fd)
                else:
                    os.unlink(entry.name, dir_fd=entry.parent_fd)
            except OSError:
                raise TurnDirectoryCleanupError(
                    "Turn directory could not be safely removed."
                ) from None

        current_root = os.stat(turn_dir.name, dir_fd=owner_fd, follow_symlinks=False)
        if (current_root.st_dev, current_root.st_ino) != root_identity:
            raise TurnDirectoryCleanupError("Turn directory changed during cleanup.")
        os.rmdir(turn_dir.name, dir_fd=owner_fd)
    except OSError:
        raise TurnDirectoryCleanupError(
            "Turn directory could not be safely inspected."
        ) from None
    finally:
        for descriptor in reversed(opened_directories):
            try:
                os.close(descriptor)
            except OSError:
                pass
        if root_fd >= 0:
            try:
                os.close(root_fd)
            except OSError:
                pass
        if owner_fd >= 0:
            try:
                os.close(owner_fd)
            except OSError:
                pass
