"""Pinned, executor-free Claude CLI discovery and version verification.

Mirrors the Codex binary gate's safety shape (bounded, fully-owned probe
subprocess; canonical-target validation; launcher-path preservation) but raises
Claude error types and pins the Claude version. Unlike the Codex gate it takes
no credential profile - Claude resolves credentials natively, so the probe runs
in a caller-supplied working directory with a caller-supplied allowlisted
environment.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .claude_constants import SUPPORTED_CLAUDE_VERSION
from .constants import (
    MAX_VERSION_OUTPUT_BYTES,
    PROCESS_TERMINATE_GRACE_SECONDS,
    VERSION_VERIFY_TIMEOUT_SECONDS,
)
from .errors import ClaudeProviderError, claude_provider_unavailable, claude_unsupported_cli
from .process_lifecycle import (
    await_task_uninterruptibly,
    read_limited,
    spawn_owned_subprocess,
    terminate_process_group,
    wait_process_exit,
)

# `claude --version` prints e.g. "2.1.218 (Claude Code)".
_VERSION_PATTERN = re.compile(r"\A(\d+\.\d+\.\d+)[ \t]+\(Claude Code\)[ \t\r\n]*\Z")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class _ExecutableIdentity:
    path: str
    launcher: _FileIdentity
    target: _FileIdentity


_VERIFIED_EXECUTABLES: set[_ExecutableIdentity] = set()


def resolve_claude_binary(candidate: Path | None = None) -> Path:
    if candidate is None:
        discovered = shutil.which("claude")
        if not discovered:
            raise ClaudeProviderError("cli_unavailable", "Claude CLI is not installed.")
        candidate = Path(discovered)
    try:
        launcher = Path(os.path.abspath(candidate.expanduser()))
        resolved = launcher.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ClaudeProviderError("cli_unavailable", "Claude CLI is not available.") from exc
    if not resolved.is_file() or not os.access(launcher, os.X_OK):
        raise ClaudeProviderError("cli_unavailable", "Claude CLI is not executable.")
    # Preserve the launcher path (installers commonly expose the CLI through a
    # symlink beside its interpreter); the canonical target is validated above.
    return launcher


def _file_identity(info: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=info.st_dev,
        inode=info.st_ino,
        size=info.st_size,
        modified_ns=info.st_mtime_ns,
    )


def _executable_identity(binary: Path) -> _ExecutableIdentity:
    try:
        launcher = binary.lstat()
        target = binary.stat()
    except OSError:
        raise claude_unsupported_cli() from None
    return _ExecutableIdentity(
        path=str(binary),
        launcher=_file_identity(launcher),
        target=_file_identity(target),
    )


def _record_unreaped_group_best_effort(
    callback: Callable[[int], None] | None,
    pgid: int | None,
) -> None:
    if callback is None or pgid is None:
        return
    try:
        callback(pgid)
    except Exception as exc:
        logger.warning(
            "Claude version quarantine callback failed: %s",
            type(exc).__name__,
        )


async def verify_claude_version(
    binary: Path,
    environment: dict[str, str],
    cwd: Path,
    *,
    on_unreaped_group: Callable[[int], None] | None = None,
) -> None:
    """Verify the exact pinned CLI with a bounded, fully-owned subprocess.

    The probe inherits the real ``HOME`` (same env as inference), so a probe child
    that cannot be confirmed reaped is a credential-bearing leak. When that
    happens ``on_unreaped_group`` (if given) is called with the leaked process
    group id so the caller can quarantine it, and the turn fails closed with
    ``provider_unavailable`` (parity with the inference path).
    """

    identity = _executable_identity(binary)
    if identity in _VERIFIED_EXECUTABLES:
        return

    process: asyncio.subprocess.Process | None = None
    process_group_id: int | None = None
    wait_task: asyncio.Task[int] | None = None
    reader_tasks: tuple[asyncio.Task[bytes], ...] = ()
    result: tuple[int, bytes] | None = None
    pending_error: BaseException | None = None
    try:
        async with asyncio.timeout(VERSION_VERIFY_TIMEOUT_SECONDS):
            process, spawn_cancellation = await spawn_owned_subprocess(
                str(binary),
                "--version",
                task_name="claude-version-spawn",
                cwd=cwd,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
            if os.name == "posix":
                process_group_id = process.pid
            if spawn_cancellation is not None:
                raise spawn_cancellation
            stdout_reader = process.stdout
            stderr_reader = process.stderr
            if stdout_reader is None or stderr_reader is None:
                raise claude_unsupported_cli()
            wait_task = asyncio.create_task(wait_process_exit(process))
            stdout_task = asyncio.create_task(
                read_limited(stdout_reader, MAX_VERSION_OUTPUT_BYTES)
            )
            stderr_task = asyncio.create_task(
                read_limited(stderr_reader, MAX_VERSION_OUTPUT_BYTES)
            )
            reader_tasks = (stdout_task, stderr_task)
            returncode, stdout, _stderr = await asyncio.gather(
                wait_task, stdout_task, stderr_task
            )
            result = (returncode, stdout)
    except asyncio.CancelledError as exc:
        pending_error = exc
    except (ClaudeProviderError, OSError):
        pending_error = claude_unsupported_cli()
    except Exception:
        pending_error = claude_unsupported_cli()

    async def finalize() -> None:
        if process is not None:
            try:
                await terminate_process_group(
                    process,
                    process_group_id,
                    lambda _event, **_fields: None,
                    grace_seconds=PROCESS_TERMINATE_GRACE_SECONDS,
                )
            except BaseException:
                # A version-probe child that cannot be confirmed reaped is a
                # credential-bearing leak (it inherited the real HOME). Quarantine
                # its group so subsequent turns stay blocked until it is gone, and
                # fail this turn closed.
                _record_unreaped_group_best_effort(
                    on_unreaped_group,
                    process_group_id,
                )
                raise claude_provider_unavailable() from None
        for task in reader_tasks:
            if not task.done():
                task.cancel()
        if reader_tasks:
            await asyncio.gather(*reader_tasks, return_exceptions=True)
        if wait_task is not None:
            if not wait_task.done():
                wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)

    cleanup_task = asyncio.create_task(finalize(), name="claude-version-finalizer")
    initial_cancellation = (
        pending_error if isinstance(pending_error, asyncio.CancelledError) else None
    )
    try:
        _, cleanup_cancellation = await await_task_uninterruptibly(
            cleanup_task, initial_cancellation
        )
    except Exception as exc:
        pending_error = exc if isinstance(exc, ClaudeProviderError) else claude_unsupported_cli()
        cleanup_cancellation = initial_cancellation

    if pending_error is not None:
        raise pending_error
    if cleanup_cancellation is not None:
        raise cleanup_cancellation
    if result is None:
        raise claude_unsupported_cli()
    returncode, stdout = result
    try:
        rendered = stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise claude_unsupported_cli() from None
    match = _VERSION_PATTERN.fullmatch(rendered)
    if returncode != 0 or match is None or match.group(1) != SUPPORTED_CLAUDE_VERSION:
        raise claude_unsupported_cli()
    _VERIFIED_EXECUTABLES.add(identity)
