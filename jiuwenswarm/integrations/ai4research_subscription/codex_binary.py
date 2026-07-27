"""Pinned, executor-free Codex CLI discovery shared by every start path."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .constants import (
    MAX_VERSION_OUTPUT_BYTES,
    PROCESS_TERMINATE_GRACE_SECONDS,
    SUPPORTED_CODEX_VERSION,
    VERSION_VERIFY_TIMEOUT_SECONDS,
)
from .errors import CodexProviderError, unsupported_cli
from .process_lifecycle import (
    await_task_uninterruptibly,
    read_limited,
    spawn_owned_subprocess,
    terminate_process_group,
    wait_process_exit,
)
from .profiles import CodexProfile
from .quarantine import quarantine_ownership


_VERSION_PATTERN = re.compile(r"\Acodex-cli[ \t]+(\d+\.\d+\.\d+)[ \t\r\n]*\Z")


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


def resolve_codex_binary(candidate: Path | None = None) -> Path:
    if candidate is None:
        discovered = shutil.which("codex")
        if not discovered:
            raise CodexProviderError("cli_unavailable", "Codex CLI is not installed.")
        candidate = Path(discovered)
    try:
        launcher = Path(os.path.abspath(candidate.expanduser()))
        resolved = launcher.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CodexProviderError("cli_unavailable", "Codex CLI is not available.") from exc
    if not resolved.is_file() or not os.access(launcher, os.X_OK):
        raise CodexProviderError("cli_unavailable", "Codex CLI is not executable.")
    # npm and other supported installers commonly expose Codex through a launcher
    # symlink beside its interpreter (for example /usr/local/bin/node).  Validate
    # the canonical target above, but preserve the launcher path so the allowlisted
    # child PATH can include that interpreter directory.
    return launcher


def _file_identity(info: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=info.st_dev,
        inode=info.st_ino,
        size=info.st_size,
        modified_ns=info.st_mtime_ns,
    )


def _executable_identity(binary: Path) -> _ExecutableIdentity:
    """Identify both a launcher and its target so replacements invalidate the cache."""

    try:
        launcher = binary.lstat()
        target = binary.stat()
    except OSError:
        raise unsupported_cli() from None
    return _ExecutableIdentity(
        path=str(binary),
        launcher=_file_identity(launcher),
        target=_file_identity(target),
    )


async def verify_codex_version(
    binary: Path,
    environment: dict[str, str],
    profile: CodexProfile,
) -> None:
    """Verify the exact pinned CLI with a bounded, fully-owned subprocess."""

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
                task_name="codex-version-spawn",
                cwd=profile.runtime_home,
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
                raise unsupported_cli()
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
    except (CodexProviderError, OSError):
        pending_error = unsupported_cli()
    except Exception:
        pending_error = unsupported_cli()

    async def finalize() -> None:
        cleanup_error: BaseException | None = None
        if process is not None:
            try:
                await terminate_process_group(
                    process,
                    process_group_id,
                    lambda _event, **_fields: None,
                    grace_seconds=PROCESS_TERMINATE_GRACE_SECONDS,
                )
            except BaseException as exc:
                cleanup_error = exc
        for task in reader_tasks:
            if not task.done():
                task.cancel()
        if reader_tasks:
            await asyncio.gather(*reader_tasks, return_exceptions=True)
        if wait_task is not None:
            if not wait_task.done():
                wait_task.cancel()
            await asyncio.gather(wait_task, return_exceptions=True)
        if cleanup_error is not None:
            try:
                quarantine_ownership(
                    profile,
                    process=process,
                    pgid=process_group_id,
                )
            except BaseException:
                pass
            raise CodexProviderError(
                "provider_quarantined",
                "Codex is unavailable until uncertain process ownership is safely reconciled.",
            ) from None

    cleanup_task = asyncio.create_task(finalize(), name="codex-version-finalizer")
    initial_cancellation = (
        pending_error if isinstance(pending_error, asyncio.CancelledError) else None
    )
    try:
        _, cleanup_cancellation = await await_task_uninterruptibly(
            cleanup_task,
            initial_cancellation,
        )
    except Exception as exc:
        pending_error = (
            exc
            if isinstance(exc, CodexProviderError) and exc.code == "provider_quarantined"
            else unsupported_cli()
        )
        cleanup_cancellation = initial_cancellation

    if pending_error is not None:
        raise pending_error
    if cleanup_cancellation is not None:
        raise cleanup_cancellation
    if result is None:
        raise unsupported_cli()
    returncode, stdout = result
    try:
        rendered = stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise unsupported_cli() from None
    match = _VERSION_PATTERN.fullmatch(rendered)
    if (
        returncode != 0
        or match is None
        or match.group(1) != SUPPORTED_CODEX_VERSION
    ):
        raise unsupported_cli()
    _VERIFIED_EXECUTABLES.add(identity)
