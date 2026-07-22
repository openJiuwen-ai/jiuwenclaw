"""Cancellation-safe lifecycle helpers for provider-owned subprocesses."""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from .errors import CodexProviderError


_T = TypeVar("_T")
_PROCESS_SCAN_INTERVAL_SECONDS = 0.05


class ProcessTreeCleanupError(RuntimeError):
    """Raised when full ownership closure cannot be proved."""


async def read_limited(stream: asyncio.StreamReader, limit: int) -> bytes:
    """Read a pipe without allowing an unbounded in-memory response."""

    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await stream.read(min(64 * 1024, limit + 1))
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > limit:
            raise CodexProviderError(
                "output_too_large", "Codex exceeded the provider output limit."
            )
        chunks.append(chunk)


async def wait_process_exit(process: asyncio.subprocess.Process) -> int:
    """Wait for the transport return code without late waiter registration."""

    while process.returncode is None:
        await asyncio.sleep(0.005)
    return process.returncode


def process_group_snapshot(pgid: int | None) -> list[dict[str, int | str]]:
    """Return non-secret Linux identities without argv, env, cwd, or comm."""

    if os.name != "posix" or pgid is None or not Path("/proc").is_dir():
        return []
    try:
        uptime = float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
        ticks = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        uptime = 0.0
        ticks = 1
    members: list[dict[str, int | str]] = []
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            raw = stat_path.read_text(encoding="utf-8")
            closing = raw.rfind(")")
            pid = int(raw[: raw.find(" ")])
            fields = raw[closing + 2 :].split()
            process_pgid = int(fields[2])
            if process_pgid != pgid:
                continue
            start_ticks = int(fields[19])
            members.append(
                {
                    "pid": pid,
                    "ppid": int(fields[1]),
                    "pgid": process_pgid,
                    "sid": int(fields[3]),
                    "state": fields[0],
                    "start_ticks": start_ticks,
                    "etimes": max(0, int(uptime - (start_ticks / ticks))),
                }
            )
        except (FileNotFoundError, OSError, ValueError, IndexError):
            continue
    return sorted(members, key=lambda item: int(item["pid"]))


def current_boot_identity() -> str | None:
    """Return a non-secret kernel boot identity when Linux exposes one."""

    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        return None
    return value or None


def process_group_is_empty(pgid: int | None) -> bool:
    if os.name != "posix" or pgid is None:
        return True
    if Path("/proc").is_dir():
        return not process_group_snapshot(pgid)
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _live_process_group_exists(pgid: int | None) -> bool:
    if os.name != "posix" or pgid is None:
        return False
    if Path("/proc").is_dir():
        return any(
            member["state"] != "Z" for member in process_group_snapshot(pgid)
        )
    return not process_group_is_empty(pgid)


async def _wait_for_group_exit(
    pgid: int | None,
    deadline: float,
    *,
    include_zombies: bool,
    scan_counter: list[int],
) -> bool:
    while True:
        scan_counter[0] += 1
        exists = (
            not process_group_is_empty(pgid)
            if include_zombies
            else _live_process_group_exists(pgid)
        )
        if not exists:
            return True
        now = asyncio.get_running_loop().time()
        if now >= deadline:
            return False
        await asyncio.sleep(min(_PROCESS_SCAN_INTERVAL_SECONDS, deadline - now))


async def terminate_process_group(
    process: asyncio.subprocess.Process,
    pgid: int | None,
    observe: Callable[..., None],
    *,
    grace_seconds: float,
) -> None:
    """Terminate and prove actual group emptiness within one total deadline."""

    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + grace_seconds
    scans = [0]
    members = process_group_snapshot(pgid)
    for member in members:
        observe("process_observed", **member)
    group_exists = not process_group_is_empty(pgid)
    live_group_exists = _live_process_group_exists(pgid)
    observe(
        "cleanup_started",
        pgid=pgid,
        group_empty=not group_exists,
        live_group_empty=not live_group_exists,
        zombie_count=sum(member["state"] == "Z" for member in members),
        cleanup_deadline_seconds=grace_seconds,
    )

    if os.name == "posix":
        if live_group_exists and pgid is not None:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    elif process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass

    term_deadline = started + (grace_seconds / 2)
    live_group_empty = await _wait_for_group_exit(
        pgid,
        term_deadline,
        include_zombies=False,
        scan_counter=scans,
    )
    if not live_group_empty or (os.name != "posix" and process.returncode is None):
        try:
            if os.name == "posix" and pgid is not None and _live_process_group_exists(pgid):
                os.killpg(pgid, signal.SIGKILL)
            elif os.name != "posix" and process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass

    final_group_empty = os.name != "posix"
    try:
        async with asyncio.timeout_at(deadline):
            await wait_process_exit(process)
            if os.name == "posix":
                final_group_empty = await _wait_for_group_exit(
                    pgid,
                    deadline,
                    include_zombies=True,
                    scan_counter=scans,
                )
    except TimeoutError:
        pass

    remaining = process_group_snapshot(pgid)
    live_remaining = [member for member in remaining if member["state"] != "Z"]
    elapsed = max(0.0, loop.time() - started)
    cleanup_complete = (
        final_group_empty and not remaining and process.returncode is not None
    )
    live_group_empty = not live_remaining if remaining else final_group_empty
    event = "group_reaped" if cleanup_complete else "cleanup_failed"
    observe(
        event,
        pgid=pgid,
        group_empty=cleanup_complete,
        live_group_empty=live_group_empty,
        zombie_count=sum(member["state"] == "Z" for member in remaining),
        cleanup_elapsed_seconds=elapsed,
        cleanup_deadline_seconds=grace_seconds,
        process_scan_count=scans[0],
    )
    if not cleanup_complete:
        raise ProcessTreeCleanupError(
            "Codex process ownership could not be closed before its deadline."
        )


async def terminate_process(
    process: asyncio.subprocess.Process,
    *,
    grace_seconds: float,
) -> None:
    """Terminate an isolated provider subprocess without exposing its command."""

    pgid = process.pid if os.name == "posix" else None
    await terminate_process_group(
        process,
        pgid,
        lambda _event, **_fields: None,
        grace_seconds=grace_seconds,
    )


async def await_task_uninterruptibly(
    cleanup_task: asyncio.Task[_T],
    initial_cancellation: asyncio.CancelledError | None = None,
) -> tuple[_T, asyncio.CancelledError | None]:
    """Finish owned cleanup despite repeated cancellation, then return cancellation."""

    current = asyncio.current_task()
    cancellation = initial_cancellation
    if cancellation is not None and current is not None:
        while current.cancelling():
            current.uncancel()
    while not cleanup_task.done():
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
            if current is not None:
                while current.cancelling():
                    current.uncancel()
    return await cleanup_task, cancellation


async def spawn_owned_subprocess(
    *argv: str,
    task_name: str,
    **kwargs: Any,
) -> tuple[asyncio.subprocess.Process, asyncio.CancelledError | None]:
    """Join a shielded spawn until it fails or yields an owned process handle."""

    spawn_task = asyncio.create_task(
        asyncio.create_subprocess_exec(*argv, **kwargs),
        name=task_name,
    )
    cancellation: asyncio.CancelledError | None = None
    try:
        process = await asyncio.shield(spawn_task)
    except asyncio.CancelledError as exc:
        process, cancellation = await await_task_uninterruptibly(spawn_task, exc)
    return process, cancellation


async def run_cleanup_uninterruptibly(
    cleanup: Awaitable[_T],
    *,
    task_name: str,
    initial_cancellation: asyncio.CancelledError | None = None,
) -> tuple[_T, asyncio.CancelledError | None]:
    """Create and shield a named cleanup task until all owned resources close."""

    task = asyncio.create_task(cleanup, name=task_name)
    return await await_task_uninterruptibly(task, initial_cancellation)
