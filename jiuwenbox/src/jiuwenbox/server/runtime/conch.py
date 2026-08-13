# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Conch sandbox runtime adapter.

Wraps the synchronous Conch Python SDK behind ``asyncio.to_thread`` so the
FastAPI event loop stays responsive. Conch exposes create/delete but not
stop; ``stop()`` raises and callers should delete or restart (cold recreate).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from jiuwenbox.models.policy import ConchNetworkPolicy, SecurityPolicy
from jiuwenbox.models.sandbox import (
    BackgroundExecResult,
    BackgroundJobStatus,
    BackgroundJobSummary,
    ExecResult,
    KillBackgroundJobResult,
)
from jiuwenbox.server.conch_policy import (
    build_conch_resource_kwargs,
    map_conch_network_policy,
    map_conch_volume_mounts,
    merge_conch_create_env,
    resolve_conch_template_id,
)
from jiuwenbox.server.runtime.base import (
    RuntimeAdapter,
    RuntimeBackgroundExecRequest,
    RuntimeExecRequest,
    RuntimeFileOpResult,
)
from jiuwenbox.server.runtime.errors import BackgroundJobNotFoundError

logger = logging.getLogger(__name__)

_CONCH_LIST_DEFAULT_MAX_DEPTH = 8
_CONCH_DELETE_WAIT_SECONDS = 30.0
_CONCH_DELETE_POLL_INTERVAL_SECONDS = 0.5
_CONCH_RECREATE_ATTEMPTS = 5
_CONCH_DELETE_ATTEMPTS = 5
_CONCH_DELETE_RETRY_SECONDS = 0.5


def _is_conch_not_found(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "not found" in text or "404" in text


def _is_conch_already_exists(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "already exists" in text or "conflict" in text


def _is_conch_delete_retryable(exc: BaseException) -> bool:
    """conchd network-slot release can race with warm-pool refill / CNI DEL."""
    text = str(exc).lower()
    return (
        "slot queue is full" in text
        or "failed to release network slot" in text
        or "failed to enqueue released network slot" in text
        or "teardown network slot" in text
        or "resource busy" in text
        or ("chain '" in text and "does not exist" in text)
        or ("cni-" in text and "does not exist" in text)
    )


@dataclass
class _ConchBackgroundJob:
    job_id: str
    sandbox_id: str
    command: list[str]
    pid: int | None
    workdir: str | None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    exit_code: int | None = None
    # Keep the SDK handle so we can drain its event stream; Conch removes
    # finished processes from ``commands.list()``, so list-only sync misses exits.
    command_handle: Any | None = None
    watch_task: asyncio.Task[None] | None = None


def _import_conch():
    """Lazy-import Conch SDK so default bwrap installs need no Conch package."""
    try:
        from conch import (  # type: ignore[import-not-found]
            CommandExitException,
            InvalidArgumentError,
            NotFoundError,
            Sandbox,
            SandboxError,
            TimeoutException,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Conch SDK is not installed. Install the local package with "
            "`pip install -e <path-to-Conch/sdk>` and ensure CONCH_SDK_CONFIG "
            "points at a valid SDK config."
        ) from exc
    return (
        Sandbox,
        CommandExitException,
        TimeoutException,
        NotFoundError,
        InvalidArgumentError,
        SandboxError,
    )


def _parse_conch_timestamp(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    text = value.strip()
    if not text:
        return fallback
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return fallback


def _entry_to_item(entry: Any) -> dict[str, Any]:
    path = getattr(entry, "path", "") or ""
    name = getattr(entry, "name", "") or Path(path).name
    is_directory = bool(getattr(entry, "is_directory", False))
    file_type = getattr(entry, "type", None)
    type_value = None
    if file_type is not None:
        type_value = getattr(file_type, "value", str(file_type))
    elif is_directory:
        type_value = "dir"
    else:
        type_value = "file"
    return {
        "name": name,
        "path": path,
        "size": int(getattr(entry, "size", 0) or 0),
        "is_directory": is_directory,
        "modified_time": getattr(entry, "modified_time", "") or None,
        "type": type_value,
        "permissions": getattr(entry, "permissions", "") or None,
    }


class ConchRuntime(RuntimeAdapter):
    """Runtime adapter backed by the Conch control plane + guest agent."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        # Serialize create/delete against conchd so concurrent CNI teardown
        # races (warm-pool refill + slot release) are less likely.
        self._lifecycle_lock = asyncio.Lock()
        self._sandboxes: dict[str, Any] = {}
        self._create_env: dict[str, dict[str, str]] = {}
        self._background_jobs: dict[str, dict[str, _ConchBackgroundJob]] = {}
        self._policy_paths: dict[str, Path] = {}

    async def create(
        self,
        sandbox_id: str,
        policy_path: Path,
        env: dict[str, str] | None = None,
    ) -> int | None:
        async with self._lock:
            existing = self._sandboxes.get(sandbox_id)
            if existing is not None:
                raise RuntimeError(f"Conch sandbox {sandbox_id} already has an active handle")

        policy = SecurityPolicy.model_validate(
            yaml.safe_load(Path(policy_path).read_text())
        )
        template_id = resolve_conch_template_id(policy)
        volume_mounts = map_conch_volume_mounts(policy)
        network = map_conch_network_policy(policy.conch.network, omit_empty=True)
        resource_kwargs = build_conch_resource_kwargs(policy.conch)
        # Conch env comes from policy.conch.env (+ create API env override).
        # Top-level SecurityPolicy.environment is bwrap-only and must not merge.
        create_env = merge_conch_create_env(policy.conch.env, env)

        (
            Sandbox,
            _CommandExitException,
            _TimeoutException,
            _NotFoundError,
            _InvalidArgumentError,
            _SandboxError,
        ) = _import_conch()

        def _create() -> Any:
            return Sandbox.create(
                template_id=template_id,
                sandbox_id=sandbox_id,
                volume_mounts=volume_mounts or None,
                env=create_env or None,
                network=network,
                **resource_kwargs,
            )

        async with self._lifecycle_lock:
            handle = await asyncio.to_thread(_create)
        async with self._lock:
            self._sandboxes[sandbox_id] = handle
            self._create_env[sandbox_id] = create_env
            self._policy_paths[sandbox_id] = Path(policy_path)
            self._background_jobs.setdefault(sandbox_id, {})
        return None

    async def stop(self, sandbox_id: str, timeout: float = 10.0) -> None:
        del timeout
        raise RuntimeError(
            f"Conch sandbox '{sandbox_id}' does not support stop; "
            "use delete to destroy it or restart for cold recreate"
        )

    async def cleanup(self, sandbox_id: str) -> None:
        await self._delete_handle(sandbox_id, missing_ok=True)

    async def recreate(
        self,
        sandbox_id: str,
        policy_path: Path,
        env: dict[str, str] | None = None,
    ) -> int | None:
        """Cold recreate: ensure deleted in conchd, then create with the same id."""
        await self._delete_handle(sandbox_id, missing_ok=True)
        last_error: Exception | None = None
        for attempt in range(1, _CONCH_RECREATE_ATTEMPTS + 1):
            try:
                await self._ensure_deleted(sandbox_id)
                return await self.create(
                    sandbox_id=sandbox_id,
                    policy_path=policy_path,
                    env=env,
                )
            except Exception as exc:
                last_error = exc
                if not _is_conch_already_exists(exc) or attempt >= _CONCH_RECREATE_ATTEMPTS:
                    raise
                logger.warning(
                    "Conch recreate hit id conflict for %s (attempt %d/%d): %s",
                    sandbox_id,
                    attempt,
                    _CONCH_RECREATE_ATTEMPTS,
                    exc,
                )
                await self._delete_in_conchd(sandbox_id)
                await asyncio.sleep(_CONCH_DELETE_POLL_INTERVAL_SECONDS)
        assert last_error is not None
        raise last_error

    async def _sandbox_present(self, sandbox_id: str) -> bool:
        Sandbox, *_ = _import_conch()

        def _probe() -> bool:
            try:
                Sandbox.get(sandbox_id)
                return True
            except Exception as exc:
                if _is_conch_not_found(exc):
                    return False
                # Unexpected get failures must not look like "already gone".
                raise RuntimeError(
                    f"Conch get({sandbox_id}) failed while probing presence: {exc}"
                ) from exc

        return await asyncio.to_thread(_probe)

    async def _delete_in_conchd(
        self,
        sandbox_id: str,
        *,
        handle: Any | None = None,
    ) -> None:
        """Delete via SDK; retry transient network-slot races; never swallow failures."""
        Sandbox, *_ = _import_conch()
        active_handle = handle

        def _delete() -> None:
            if active_handle is not None:
                active_handle.delete(sandbox_id=sandbox_id)
            else:
                Sandbox.delete_sandbox(sandbox_id)

        last_error: Exception | None = None
        for attempt in range(1, _CONCH_DELETE_ATTEMPTS + 1):
            try:
                async with self._lifecycle_lock:
                    await asyncio.to_thread(_delete)
                return
            except Exception as exc:
                if _is_conch_not_found(exc):
                    if last_error is not None:
                        raise RuntimeError(
                            f"Failed to delete Conch sandbox '{sandbox_id}': "
                            f"{last_error}"
                        ) from last_error
                    return
                last_error = exc
                # Later retries use the static API in case the handle is stale
                # after a partial conchd cleanup.
                active_handle = None
                if attempt >= _CONCH_DELETE_ATTEMPTS or not _is_conch_delete_retryable(
                    exc
                ):
                    break
                logger.warning(
                    "Conch delete for %s failed (attempt %d/%d): %s; retrying",
                    sandbox_id,
                    attempt,
                    _CONCH_DELETE_ATTEMPTS,
                    exc,
                )
                await asyncio.sleep(_CONCH_DELETE_RETRY_SECONDS * attempt)

        assert last_error is not None
        raise RuntimeError(
            f"Failed to delete Conch sandbox '{sandbox_id}': {last_error}"
        ) from last_error

    async def _ensure_deleted(self, sandbox_id: str) -> None:
        """Delete and wait until conchd no longer returns the sandbox."""
        await self._delete_in_conchd(sandbox_id)
        deadline = time.monotonic() + _CONCH_DELETE_WAIT_SECONDS

        while time.monotonic() < deadline:
            if not await self._sandbox_present(sandbox_id):
                return
            await self._delete_in_conchd(sandbox_id)
            await asyncio.sleep(_CONCH_DELETE_POLL_INTERVAL_SECONDS)
        raise RuntimeError(
            f"Conch sandbox '{sandbox_id}' still present after "
            f"{_CONCH_DELETE_WAIT_SECONDS:.0f}s delete wait"
        )

    async def _delete_handle(self, sandbox_id: str, *, missing_ok: bool) -> None:
        async with self._lock:
            handle = self._sandboxes.pop(sandbox_id, None)
            self._create_env.pop(sandbox_id, None)
            self._policy_paths.pop(sandbox_id, None)
            jobs = self._background_jobs.pop(sandbox_id, {})

        for job in jobs.values():
            watch = job.watch_task
            if watch is not None and not watch.done():
                watch.cancel()
            command_handle = job.command_handle
            if command_handle is not None:
                disconnect = getattr(command_handle, "disconnect", None)
                if callable(disconnect):
                    try:
                        disconnect()
                    except Exception:
                        logger.debug(
                            "Conch command handle disconnect failed for %s/%s",
                            sandbox_id,
                            job.job_id,
                            exc_info=True,
                        )

        if handle is None:
            if missing_ok:
                await self._delete_in_conchd(sandbox_id)
                return
            raise RuntimeError(f"Conch sandbox {sandbox_id} is not active")

        await self._delete_in_conchd(sandbox_id, handle=handle)

    async def is_running(self, sandbox_id: str) -> bool:
        handle = await self._get_handle(sandbox_id)
        if handle is None:
            return False

        def _health() -> dict[str, Any]:
            return handle.health_check()

        try:
            result = await asyncio.to_thread(_health)
        except Exception:
            return False
        status = str(result.get("status", "")).upper()
        return status == "OK"

    async def get_sandbox_ip_address(self, sandbox_id: str) -> str | None:
        handle = await self._get_handle(sandbox_id)
        if handle is None:
            return None
        ip = getattr(handle, "ip", None)
        if isinstance(ip, str) and ip.strip():
            return ip.strip()
        return None

    async def update_network_policy(
        self,
        sandbox_id: str,
        network_policy: ConchNetworkPolicy,
    ) -> None:
        handle = await self._require_handle(sandbox_id)
        payload = map_conch_network_policy(network_policy, omit_empty=False)
        assert payload is not None

        def _update() -> None:
            handle.update_network(
                allow_out=list(payload["allowOut"]),
                deny_out=list(payload["denyOut"]),
                allow_in=list(payload["allowIn"]),
                deny_in=list(payload["denyIn"]),
                allow_internet_access=bool(payload["allow_internet_access"]),
            )

        await asyncio.to_thread(_update)

    async def exec(
        self,
        sandbox_id: str,
        request: RuntimeExecRequest,
    ) -> ExecResult:
        handle = await self._require_handle(sandbox_id)
        if not request.command:
            raise ValueError("command must not be empty")
        cmd = request.command[0]
        args = list(request.command[1:])
        stdin = request.stdin_data
        (
            _Sandbox,
            CommandExitException,
            TimeoutException,
            _NotFoundError,
            _InvalidArgumentError,
            _SandboxError,
        ) = _import_conch()

        def _run() -> ExecResult:
            try:
                result = handle.commands.run(
                    cmd=cmd,
                    args=args,
                    cwd=request.workdir,
                    env=request.env,
                    stdin=stdin,
                    timeout=request.timeout,
                    background=False,
                )
            except CommandExitException as exc:
                return ExecResult(
                    exit_code=int(exc.exit_code),
                    stdout=exc.stdout or "",
                    stderr=exc.stderr or "",
                )
            except TimeoutException as exc:
                return ExecResult(
                    exit_code=124,
                    stdout="",
                    stderr=str(exc) or "command timed out",
                )
            return ExecResult(
                exit_code=int(getattr(result, "exit_code", 0) or 0),
                stdout=getattr(result, "stdout", "") or "",
                stderr=getattr(result, "stderr", "") or "",
            )

        return await asyncio.to_thread(_run)

    async def exec_background(
        self,
        sandbox_id: str,
        request: RuntimeBackgroundExecRequest,
    ) -> BackgroundExecResult:
        handle = await self._require_handle(sandbox_id)
        if not request.command:
            return BackgroundExecResult(
                started=False,
                command=list(request.command),
                error_message="command must not be empty",
            )
        cmd = request.command[0]
        args = list(request.command[1:])
        (
            _Sandbox,
            _CommandExitException,
            _TimeoutException,
            _NotFoundError,
            InvalidArgumentError,
            SandboxError,
        ) = _import_conch()

        def _start() -> Any:
            return handle.commands.run(
                cmd=cmd,
                args=args,
                cwd=request.workdir,
                env=request.env,
                stdin=request.stdin_data,
                background=True,
                tag=request.job_id,
            )

        try:
            command_handle = await asyncio.to_thread(_start)
        except (InvalidArgumentError, SandboxError, RuntimeError) as exc:
            message = str(exc)
            lower = message.lower()
            if "already" in lower or "duplicate" in lower or "conflict" in lower:
                return BackgroundExecResult(
                    started=False,
                    command=list(request.command),
                    job_id=request.job_id,
                    error_message=message,
                )
            return BackgroundExecResult(
                started=False,
                command=list(request.command),
                job_id=request.job_id,
                error_message=message,
            )

        pid = getattr(command_handle, "pid", None)
        pid_int = pid if isinstance(pid, int) else None
        job = _ConchBackgroundJob(
            job_id=request.job_id,
            sandbox_id=sandbox_id,
            command=list(request.command),
            pid=pid_int,
            workdir=request.workdir,
            command_handle=command_handle,
        )
        async with self._lock:
            self._background_jobs.setdefault(sandbox_id, {})[request.job_id] = job
            job.watch_task = asyncio.create_task(
                self._watch_background_job(job),
                name=f"conch-bg-{sandbox_id}-{request.job_id}",
            )

        return BackgroundExecResult(
            started=True,
            job_id=request.job_id,
            pid=pid_int,
            command=list(request.command),
            running=True,
            exit_code=None,
        )

    async def get_background_job(
        self,
        sandbox_id: str,
        job_id: str,
    ) -> BackgroundJobStatus:
        job = await self._require_job(sandbox_id, job_id)
        await self._sync_job(sandbox_id, job)
        return self._job_status(job)

    async def list_background_jobs(
        self,
        sandbox_id: str,
        *,
        running_only: bool = False,
    ) -> list[BackgroundJobSummary]:
        async with self._lock:
            jobs = list(self._background_jobs.get(sandbox_id, {}).values())
        for job in jobs:
            await self._sync_job(sandbox_id, job)
        summaries = [self._job_summary(job) for job in jobs]
        if running_only:
            summaries = [item for item in summaries if item.running]
        summaries.sort(key=lambda item: item.started_at, reverse=True)
        return summaries

    async def kill_background_job(
        self,
        sandbox_id: str,
        job_id: str,
        signum: int = 15,
    ) -> KillBackgroundJobResult:
        handle = await self._require_handle(sandbox_id)
        job = await self._require_job(sandbox_id, job_id)
        await self._sync_job(sandbox_id, job)
        if job.exit_code is not None:
            return KillBackgroundJobResult(
                job_id=job_id,
                killed=False,
                reason="already_exited",
                exit_code=job.exit_code,
            )

        def _kill() -> bool:
            return bool(handle.commands.kill(tag=job_id, signal=signum))

        killed = await asyncio.to_thread(_kill)
        await self._sync_job(sandbox_id, job)
        if not killed and job.exit_code is not None:
            return KillBackgroundJobResult(
                job_id=job_id,
                killed=False,
                reason="already_exited",
                exit_code=job.exit_code,
            )
        if not killed:
            return KillBackgroundJobResult(
                job_id=job_id,
                killed=False,
                reason="permission_denied",
                exit_code=job.exit_code,
            )
        return KillBackgroundJobResult(
            job_id=job_id,
            killed=True,
            reason="signaled",
            exit_code=job.exit_code,
        )

    async def write_file(
        self,
        sandbox_id: str,
        sandbox_path: str,
        content: bytes,
        *,
        mkdir_parents: bool = True,
        mode: int | None = None,
    ) -> RuntimeFileOpResult:
        del mkdir_parents, mode  # Conch files.write has no mode/mkdir controls.
        handle = await self._require_handle(sandbox_id)
        (
            _Sandbox,
            _CommandExitException,
            _TimeoutException,
            NotFoundError,
            InvalidArgumentError,
            SandboxError,
        ) = _import_conch()

        def _write() -> None:
            handle.files.write(sandbox_path, content)

        try:
            await asyncio.to_thread(_write)
        except NotFoundError as exc:
            return RuntimeFileOpResult(ok=False, error="not_found", detail=str(exc))
        except InvalidArgumentError as exc:
            return RuntimeFileOpResult(ok=False, error="invalid_argument", detail=str(exc))
        except SandboxError as exc:
            return RuntimeFileOpResult(ok=False, error="io_error", detail=str(exc))
        except Exception as exc:
            return RuntimeFileOpResult(ok=False, error="io_error", detail=str(exc))
        return RuntimeFileOpResult(ok=True)

    async def read_file(
        self,
        sandbox_id: str,
        sandbox_path: str,
    ) -> RuntimeFileOpResult:
        handle = await self._require_handle(sandbox_id)
        (
            _Sandbox,
            _CommandExitException,
            _TimeoutException,
            NotFoundError,
            InvalidArgumentError,
            SandboxError,
        ) = _import_conch()

        def _read() -> bytes:
            return handle.files.read(sandbox_path, format="bytes")

        try:
            content = await asyncio.to_thread(_read)
        except NotFoundError as exc:
            return RuntimeFileOpResult(ok=False, error="not_found", detail=str(exc))
        except InvalidArgumentError as exc:
            return RuntimeFileOpResult(ok=False, error="invalid_argument", detail=str(exc))
        except IsADirectoryError as exc:
            return RuntimeFileOpResult(ok=False, error="is_directory", detail=str(exc))
        except SandboxError as exc:
            message = str(exc).lower()
            if "directory" in message:
                return RuntimeFileOpResult(ok=False, error="is_directory", detail=str(exc))
            return RuntimeFileOpResult(ok=False, error="io_error", detail=str(exc))
        except Exception as exc:
            return RuntimeFileOpResult(ok=False, error="io_error", detail=str(exc))
        return RuntimeFileOpResult(ok=True, content=content)

    async def list_dir(
        self,
        sandbox_id: str,
        sandbox_path: str,
        *,
        recursive: bool = False,
        max_depth: int | None = None,
        include_files: bool = True,
        include_dirs: bool = True,
    ) -> RuntimeFileOpResult:
        handle = await self._require_handle(sandbox_id)
        if recursive:
            depth = max_depth if max_depth is not None else _CONCH_LIST_DEFAULT_MAX_DEPTH
        else:
            depth = 1
        (
            _Sandbox,
            _CommandExitException,
            _TimeoutException,
            NotFoundError,
            InvalidArgumentError,
            SandboxError,
        ) = _import_conch()

        def _list() -> list[Any]:
            return handle.files.list(sandbox_path, depth=depth)

        try:
            entries = await asyncio.to_thread(_list)
        except NotFoundError as exc:
            return RuntimeFileOpResult(ok=False, error="not_found", detail=str(exc))
        except InvalidArgumentError as exc:
            return RuntimeFileOpResult(ok=False, error="invalid_argument", detail=str(exc))
        except SandboxError as exc:
            return RuntimeFileOpResult(ok=False, error="io_error", detail=str(exc))
        except Exception as exc:
            return RuntimeFileOpResult(ok=False, error="io_error", detail=str(exc))

        items: list[dict[str, Any]] = []
        for entry in entries:
            item = _entry_to_item(entry)
            if item["is_directory"]:
                if include_dirs:
                    items.append(item)
            elif include_files:
                items.append(item)
        items.sort(key=lambda row: str(row.get("path") or ""))
        return RuntimeFileOpResult(ok=True, items=items)

    async def search_files(
        self,
        sandbox_id: str,
        sandbox_path: str,
        patterns: list[str],
        *,
        exclude_patterns: list[str] | None = None,
    ) -> RuntimeFileOpResult:
        handle = await self._require_handle(sandbox_id)
        if not patterns:
            return RuntimeFileOpResult(ok=False, error="invalid_argument", detail="patterns required")
        # Conch search accepts a single pattern; join alternatives with '|' is not
        # supported, so use the first pattern (API currently exposes one pattern).
        pattern = patterns[0]
        (
            _Sandbox,
            _CommandExitException,
            _TimeoutException,
            NotFoundError,
            InvalidArgumentError,
            SandboxError,
        ) = _import_conch()

        def _search() -> list[Any]:
            return handle.files.search(
                sandbox_path,
                pattern,
                exclude_patterns=exclude_patterns,
            )

        try:
            entries = await asyncio.to_thread(_search)
        except NotFoundError as exc:
            return RuntimeFileOpResult(ok=False, error="not_found", detail=str(exc))
        except InvalidArgumentError as exc:
            return RuntimeFileOpResult(ok=False, error="invalid_argument", detail=str(exc))
        except SandboxError as exc:
            return RuntimeFileOpResult(ok=False, error="io_error", detail=str(exc))
        except Exception as exc:
            return RuntimeFileOpResult(ok=False, error="io_error", detail=str(exc))

        items = [_entry_to_item(entry) for entry in entries]
        items.sort(key=lambda row: str(row.get("path") or ""))
        return RuntimeFileOpResult(ok=True, items=items)

    async def _get_handle(self, sandbox_id: str) -> Any | None:
        async with self._lock:
            return self._sandboxes.get(sandbox_id)

    async def _require_handle(self, sandbox_id: str) -> Any:
        handle = await self._get_handle(sandbox_id)
        if handle is None:
            raise RuntimeError(f"Conch sandbox {sandbox_id} is not active")
        return handle

    async def _require_job(self, sandbox_id: str, job_id: str) -> _ConchBackgroundJob:
        async with self._lock:
            job = self._background_jobs.get(sandbox_id, {}).get(job_id)
        if job is None:
            raise BackgroundJobNotFoundError(
                f"Background job '{job_id}' not found in sandbox '{sandbox_id}'",
            )
        return job

    async def _watch_background_job(self, job: _ConchBackgroundJob) -> None:
        """Drain the Conch CommandHandle event stream until the process ends."""
        handle = job.command_handle
        if handle is None:
            return
        (
            _Sandbox,
            CommandExitException,
            _TimeoutException,
            _NotFoundError,
            _InvalidArgumentError,
            _SandboxError,
        ) = _import_conch()

        def _wait() -> int:
            try:
                result = handle.wait()
                return int(getattr(result, "exit_code", 0) or 0)
            except CommandExitException as exc:
                return int(exc.exit_code)
            except Exception as exc:
                logger.debug(
                    "Conch background wait for %s/%s failed: %s",
                    job.sandbox_id,
                    job.job_id,
                    exc,
                    exc_info=True,
                )
                return -1

        try:
            exit_code = await asyncio.to_thread(_wait)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug(
                "Conch background watcher crashed for %s/%s",
                job.sandbox_id,
                job.job_id,
                exc_info=True,
            )
            exit_code = -1

        if job.exit_code is None:
            job.exit_code = exit_code
            job.finished_at = datetime.now(timezone.utc)

    async def _sync_job(self, sandbox_id: str, job: _ConchBackgroundJob) -> None:
        if job.exit_code is not None:
            return

        # Prefer the watcher / handle wait result; list() alone is racy because
        # conchd removes finished processes from the registry.
        watch = job.watch_task
        if watch is not None and watch.done():
            if job.exit_code is None:
                job.exit_code = -1
                job.finished_at = datetime.now(timezone.utc)
            return

        handle = await self._get_handle(sandbox_id)
        if handle is None:
            return

        def _list() -> list[Any]:
            return handle.commands.list()

        try:
            processes = await asyncio.to_thread(_list)
        except Exception:
            logger.debug(
                "Failed to list Conch processes for sandbox %s",
                sandbox_id,
                exc_info=True,
            )
            return

        match = None
        for process in processes:
            if getattr(process, "tag", None) == job.job_id:
                match = process
                break
        if match is None:
            # Process left the registry (typically after exit). If the watcher
            # has not finished yet, give it a brief moment to record exit_code.
            if watch is not None and not watch.done():
                try:
                    await asyncio.wait_for(asyncio.shield(watch), timeout=0.5)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
            if job.exit_code is None:
                job.exit_code = 0
                job.finished_at = datetime.now(timezone.utc)
            return

        job.pid = int(getattr(match, "pid", 0) or 0) or job.pid
        started_at = _parse_conch_timestamp(
            getattr(match, "started_at", "") or "",
            job.started_at,
        )
        job.started_at = started_at
        running = bool(getattr(match, "running", False))
        if running:
            return

        exit_code = getattr(match, "exit_code", 0)
        job.exit_code = int(exit_code) if exit_code is not None else 0
        job.finished_at = _parse_conch_timestamp(
            getattr(match, "finished_at", "") or "",
            datetime.now(timezone.utc),
        )

    @staticmethod
    def _job_status(job: _ConchBackgroundJob) -> BackgroundJobStatus:
        running = job.exit_code is None
        return BackgroundJobStatus(
            job_id=job.job_id,
            sandbox_id=job.sandbox_id,
            command=list(job.command),
            pid=job.pid,
            running=running,
            exit_code=job.exit_code,
            started_at=job.started_at,
            finished_at=job.finished_at,
            workdir=job.workdir,
        )

    @staticmethod
    def _job_summary(job: _ConchBackgroundJob) -> BackgroundJobSummary:
        running = job.exit_code is None
        return BackgroundJobSummary(
            job_id=job.job_id,
            pid=job.pid,
            command=list(job.command),
            running=running,
            exit_code=job.exit_code,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )
