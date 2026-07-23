"""Minimal bounded JSON-RPC client for the Codex App Server auth surface."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable

from .codex_binary import resolve_codex_binary, verify_codex_version
from .constants import (
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
    PROCESS_TERMINATE_GRACE_SECONDS,
)
from .errors import CodexProviderError
from .process_lifecycle import (
    await_task_uninterruptibly,
    read_limited,
    spawn_owned_subprocess,
    terminate_process,
)
from .profiles import CodexProfile, build_codex_environment
from .quarantine import (
    profile_is_quarantined,
    quarantine_ownership,
    reconcile_profile_quarantine,
)


class CodexAppServerClient:
    def __init__(self, profile: CodexProfile, *, binary_path: Path | None = None):
        self._profile = profile
        self._binary = resolve_codex_binary(binary_path)
        self._process: asyncio.subprocess.Process | None = None
        self._process_group_id: int | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=32)
        self._next_id = 1
        self._write_lock = asyncio.Lock()

    @property
    def profile(self) -> CodexProfile:
        return self._profile

    async def start(self) -> None:
        await reconcile_profile_quarantine(self._profile)
        environment = build_codex_environment(
            self._profile,
            binary=self._binary,
            temporary_dir=self._profile.runtime_home,
        )
        await verify_codex_version(
            self._binary,
            environment,
            self._profile,
        )
        process, spawn_cancellation = await spawn_owned_subprocess(
            str(self._binary),
            "app-server",
            "--listen",
            "stdio://",
            "--strict-config",
            task_name="codex-app-server-spawn",
            cwd=self._profile.runtime_home,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        self._process = process
        self._process_group_id = process.pid if os.name == "posix" else None
        try:
            if spawn_cancellation is not None:
                raise spawn_cancellation
            assert process.stdout is not None and process.stderr is not None
            self._reader_task = asyncio.create_task(self._read_frames())
            self._stderr_task = asyncio.create_task(
                read_limited(process.stderr, MAX_STDERR_BYTES)
            )
            initialized = await self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "jiuwenswarm_subscription_auth",
                        "title": "JiuwenSwarm Subscription Authentication",
                        "version": "1.0.0",
                    },
                    "capabilities": {"experimentalApi": False},
                },
                timeout=15.0,
            )
            if not isinstance(initialized, dict):
                raise CodexProviderError(
                    "auth_protocol_error", "Codex authentication initialization failed."
                )
            await self.notify("initialized", {})
        except BaseException as exc:
            cleanup_task = asyncio.create_task(self.close(), name="codex-app-server-start-finalizer")
            try:
                _, cleanup_cancellation = await await_task_uninterruptibly(
                    cleanup_task,
                    exc if isinstance(exc, asyncio.CancelledError) else None,
                )
            except BaseException as cleanup_exc:
                raise cleanup_exc
            if cleanup_cancellation is not None:
                raise cleanup_cancellation
            raise

    async def _read_frames(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        total = 0
        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break
                total += len(line)
                if len(line) > 512 * 1024 or total > MAX_STDOUT_BYTES:
                    raise CodexProviderError("output_too_large", "Codex authentication output exceeded its limit.")
                try:
                    frame = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CodexProviderError("auth_protocol_error", "Codex authentication returned malformed output.") from exc
                if not isinstance(frame, dict):
                    raise CodexProviderError("auth_protocol_error", "Codex authentication returned invalid output.")
                if "id" in frame:
                    future = self._pending.pop(frame["id"], None)
                    if future is not None and not future.done():
                        future.set_result(frame)
                elif isinstance(frame.get("method"), str):
                    try:
                        self._notifications.put_nowait(frame)
                    except asyncio.QueueFull as exc:
                        raise CodexProviderError("auth_protocol_error", "Codex authentication emitted too many events.") from exc
            raise CodexProviderError(
                "auth_protocol_error", "Codex authentication stopped unexpectedly."
            )
        except BaseException as exc:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(exc)
            self._pending.clear()
            raise

    async def _write(self, frame: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.returncode is not None or process.stdin is None:
            raise CodexProviderError("auth_protocol_error", "Codex authentication is not running.")
        payload = (json.dumps(frame, separators=(",", ":")) + "\n").encode("utf-8")
        async with self._write_lock:
            try:
                process.stdin.write(payload)
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise CodexProviderError("auth_protocol_error", "Codex authentication stopped unexpectedly.") from exc

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._write({"method": method, "params": params})

    async def request(self, method: str, params: dict[str, Any], *, timeout: float = 15.0) -> Any:
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"method": method, "id": request_id, "params": params})
            frame = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise CodexProviderError("auth_timeout", "Codex authentication timed out.") from exc
        finally:
            self._pending.pop(request_id, None)
        if not isinstance(frame, dict) or "error" in frame or "result" not in frame:
            raise CodexProviderError("auth_failed", "Codex authentication could not complete the request.")
        return frame["result"]

    async def wait_notification(
        self,
        method: str,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        reader_task = self._reader_task
        if reader_task is None:
            raise CodexProviderError(
                "auth_protocol_error", "Codex authentication is not running."
            )

        def _raise_reader_failure() -> None:
            try:
                reader_task.result()
            except asyncio.CancelledError as exc:
                raise CodexProviderError(
                    "auth_protocol_error", "Codex authentication stopped unexpectedly."
                ) from exc
            except CodexProviderError:
                raise
            except BaseException as exc:
                raise CodexProviderError(
                    "auth_protocol_error", "Codex authentication stopped unexpectedly."
                ) from exc
            raise CodexProviderError(
                "auth_protocol_error", "Codex authentication stopped unexpectedly."
            )

        async def _wait() -> dict[str, Any]:
            while True:
                notification_task = asyncio.create_task(self._notifications.get())
                try:
                    done, _pending = await asyncio.wait(
                        {notification_task, reader_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if notification_task in done:
                        frame = notification_task.result()
                        if frame.get("method") == method:
                            params = frame.get("params")
                            if isinstance(params, dict) and predicate(params):
                                return params
                    if reader_task in done:
                        _raise_reader_failure()
                finally:
                    if not notification_task.done():
                        notification_task.cancel()
                        await asyncio.gather(notification_task, return_exceptions=True)

        try:
            return await asyncio.wait_for(_wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise CodexProviderError("auth_timeout", "Codex login approval timed out.") from exc

    async def close(self) -> None:
        if profile_is_quarantined(self._profile):
            await reconcile_profile_quarantine(self._profile)
            self._process = None
            self._process_group_id = None
            self._reader_task = None
            self._stderr_task = None
            return
        process = self._process
        cleanup_error: BaseException | None = None
        if process is not None:
            if process.stdin is not None and not process.stdin.is_closing():
                process.stdin.close()
            try:
                await terminate_process(
                    process,
                    grace_seconds=PROCESS_TERMINATE_GRACE_SECONDS,
                )
            except BaseException as exc:
                cleanup_error = exc
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (self._reader_task, self._stderr_task) if task is not None),
            return_exceptions=True,
        )
        if cleanup_error is not None:
            try:
                quarantine_ownership(
                    self._profile,
                    process=process,
                    pgid=self._process_group_id,
                )
            except BaseException:
                pass
            raise CodexProviderError(
                "provider_quarantined",
                "Codex is unavailable until uncertain process ownership is safely reconciled.",
            ) from None
        self._process = None
        self._process_group_id = None
        self._reader_task = None
        self._stderr_task = None
