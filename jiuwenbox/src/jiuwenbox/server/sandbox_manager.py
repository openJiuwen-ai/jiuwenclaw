# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Sandbox lifecycle manager.

Coordinates runtime adapters, policy engine, and audit logger to manage
the full lifecycle of sandboxes: create -> start -> stop -> delete.
Persists sandbox state to disk for crash recovery.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import textwrap
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Cap stdout/stderr at this many chars per audit event. Large model
# completions can dump megabytes, which is fine to keep in the runtime
# log file but explodes the audit JSONL (one line per event, designed to
# be cheap to ``tail -f``). 4 KiB is enough to keep error messages /
# tracebacks intact while keeping the audit file bounded.
_AUDIT_OUTPUT_LIMIT = 4096


def _truncate_for_audit(text: str | None, *, limit: int = _AUDIT_OUTPUT_LIMIT) -> str:
    """Return ``text`` clipped to ``limit`` chars with a visible marker.

    The tail is preferred over the head because errors typically surface
    at the end of stderr; truncation is annotated inline so the operator
    is not misled into thinking they have the full output.
    """
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[-limit:] + f"\n[truncated, total {len(text)} chars]"

from jiuwenbox.logging_config import configure_logging
from jiuwenbox.models.common import AuditEventType
from jiuwenbox.models.policy import SecurityPolicy
from jiuwenbox.models.sandbox import (
    BackgroundExecResult,
    ExecResult,
    PolicyMode,
    SandboxPhase,
    SandboxRef,
    SandboxSpec,
)
from jiuwenbox.server.audit_logger import AuditLogger
from jiuwenbox.server.policy_engine import PolicyEngine
from jiuwenbox.server.policy_reader import PolicyReader
from jiuwenbox.server.runtime.base import RuntimeAdapter, RuntimeExecRequest
from jiuwenbox.server.runtime.process import ProcessRuntime
from jiuwenbox.server.workspace import JIUWENBOX_HOME

configure_logging()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SandboxExecRequest:
    command: list[str]
    workdir: str | None = None
    env: dict[str, str] | None = None
    stdin_data: bytes | None = None
    timeout: float | None = None


@dataclass(frozen=True)
class SandboxListRequest:
    sandbox_path: str
    recursive: bool = False
    max_depth: int | None = None
    include_files: bool = True
    include_dirs: bool = True


class SandboxNotFoundError(Exception):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        logger.error("%s: %s", self.__class__.__name__, str(self))


class SandboxStateError(Exception):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)
        logger.error("%s: %s", self.__class__.__name__, str(self))


class SandboxManager:
    """Manages sandbox lifecycle and state."""

    def __init__(
        self,
        runtime: RuntimeAdapter | None = None,
        policy_engine: PolicyEngine | None = None,
        audit_logger: AuditLogger | None = None,
        state_dir: Path | None = None,
        policy_reader: PolicyReader | None = None,
        policy_path: Path | None = None,
    ) -> None:
        self.runtime = runtime or ProcessRuntime()
        self.policy_engine = policy_engine or PolicyEngine()
        self.audit = audit_logger or AuditLogger()
        self.state_dir = state_dir or JIUWENBOX_HOME / "sandboxes"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.policy_reader = policy_reader or PolicyReader(
            policy_engine=self.policy_engine,
            policy_path=policy_path,
        )
        self.policy = self.policy_reader.load_policy()

        self._lock = asyncio.Lock()
        self._sandboxes: dict[str, SandboxRef] = {}
        self._policies: dict[str, SecurityPolicy] = {}
        # Sandbox state is treated as ephemeral: jiuwenbox starts up with an empty
        # registry, regardless of any leftover files under ``state_dir`` /
        # ``policies_dir``. The dirs themselves are recreated above so subsequent
        # ``create_sandbox`` calls can still persist YAML / JSON during a single
        # process lifetime, but nothing is read back on boot.
        #
        # On graceful shutdown ``clear_persistent_state`` wipes both dirs so a
        # subsequent jiuwenbox launch never sees stale sandbox descriptors. This
        # avoids accidentally "reviving" dead sandboxes whose backing bubblewrap
        # processes / netns / cgroup state no longer exist.

    def _resolve_effective_policy(
        self,
        policy_data: SecurityPolicy | Mapping[str, object] | None,
        policy_mode: PolicyMode,
    ) -> SecurityPolicy:
        base_policy = self.policy.model_copy(deep=True)
        if policy_data is None:
            return base_policy

        if isinstance(policy_data, SecurityPolicy):
            policy_payload: SecurityPolicy | Mapping[str, object] = policy_data
        else:
            policy_payload = dict(policy_data)

        if policy_mode == PolicyMode.APPEND:
            return self.policy_engine.merge_policy(base_policy, policy_payload)

        if isinstance(policy_payload, SecurityPolicy):
            return policy_payload.model_copy(deep=True)

        return SecurityPolicy.model_validate(policy_payload)

    def _load_state(self) -> None:
        """Load persisted sandbox state from ``state_dir``.

        No longer invoked from ``__init__``: jiuwenbox treats sandbox registry
        as ephemeral across restarts (see ``__init__`` for rationale). Kept as
        an opt-in helper for tooling that explicitly wants to inspect leftover
        state files.
        """
        for state_file in self.state_dir.glob("*.json"):
            try:
                data = json.loads(state_file.read_text())
                ref = SandboxRef.model_validate(data)
                self._sandboxes[ref.id] = ref
                logger.info("Loaded sandbox state: %s (%s)", ref.id, ref.phase.value)
            except Exception:
                logger.warning("Failed to load state from %s", state_file, exc_info=True)

    def clear_persistent_state(self) -> None:
        """Remove all sandbox / policy descriptors from disk.

        Called from the FastAPI lifespan shutdown hook so the next boot starts
        from a clean slate. The directories themselves are preserved (recreated
        if missing) so other code that grabs ``state_dir`` / ``policies_dir``
        paths during shutdown won't ``FileNotFoundError``.

        This method is *best-effort*: any single-file removal failure is logged
        but does not abort the rest of the cleanup. Note that this method only
        wipes on-disk descriptors; live ``sandbox-daemon.py`` / bubblewrap
        children are spawned with ``start_new_session=True`` (their own session
        + pgrp) so they **do not** die automatically when uvicorn exits. To
        actually tear down running sandboxes during shutdown, call
        :meth:`shutdown_all_sandboxes` first.
        """
        for label, directory, pattern in (
            ("sandbox state", self.state_dir, "*.json"),
            ("sandbox policy", self.policy_engine.policies_dir, "*.yaml"),
        ):
            if not directory.exists():
                continue
            removed = 0
            for entry in directory.glob(pattern):
                try:
                    entry.unlink()
                    removed += 1
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    logger.warning(
                        "clear_persistent_state: failed to remove %s %s: %s",
                        label,
                        entry,
                        exc,
                    )
            logger.info(
                "clear_persistent_state: removed %d %s file(s) under %s",
                removed,
                label,
                directory,
            )

    async def shutdown_all_sandboxes(self) -> None:
        """Best-effort teardown of every registered sandbox.

        Called from the FastAPI lifespan shutdown hook *before*
        :meth:`clear_persistent_state` so that the per-sandbox bubblewrap
        process group (and the ``sandbox-daemon.py`` running inside it) are
        actually terminated instead of being reparented to PID 1 when the
        jiuwenbox-server process exits. Each sandbox is torn down via
        :meth:`delete_sandbox`, which routes through
        ``runtime.cleanup`` -> ``runtime.stop`` -> ``os.killpg(SIGTERM/SIGKILL)``
        on the daemon's own session group.

        Individual failures are logged but do not abort the remaining
        teardowns; the goal is to avoid leaking orphan processes even if one
        sandbox's daemon refuses to cooperate.
        """
        async with self._lock:
            sandbox_ids = list(self._sandboxes.keys())
        if not sandbox_ids:
            logger.info("shutdown_all_sandboxes: no live sandboxes to tear down")
            return
        logger.info(
            "shutdown_all_sandboxes: tearing down %d sandbox(es): %s",
            len(sandbox_ids),
            sandbox_ids,
        )
        for sandbox_id in sandbox_ids:
            try:
                await self.delete_sandbox(sandbox_id)
            except SandboxNotFoundError:
                # Already deleted between the snapshot above and now; treat
                # as a clean teardown.
                continue
            except Exception:  # noqa: BLE001
                logger.exception(
                    "shutdown_all_sandboxes: delete_sandbox(%s) failed",
                    sandbox_id,
                )

    def register_zombie_reaper(
        self,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> bool:
        """Forward SIGCHLD reaper registration to the underlying runtime.

        Defined as a thin pass-through so the FastAPI lifespan hook can talk
        to the manager (the only object it already holds) instead of having
        to reach into ``self.runtime`` -- runtimes other than
        :class:`ProcessRuntime` may not need a reaper at all, in which case
        they can return ``True`` and the lifespan code stays uniform.
        """
        register = getattr(self.runtime, "register_zombie_reaper", None)
        if register is None:
            return True
        return register(loop)

    def unregister_zombie_reaper(
        self,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        unregister = getattr(self.runtime, "unregister_zombie_reaper", None)
        if unregister is None:
            return
        unregister(loop)

    def _save_state(self, sandbox: SandboxRef) -> None:
        """Persist a single sandbox's state to disk."""
        path = self.state_dir / f"{sandbox.id}.json"
        path.write_text(sandbox.model_dump_json(indent=2))

    def _delete_state(self, sandbox_id: str) -> None:
        path = self.state_dir / f"{sandbox_id}.json"
        path.unlink(missing_ok=True)

    def _get_sandbox(self, sandbox_id: str) -> SandboxRef:
        ref = self._sandboxes.get(sandbox_id)
        if ref is None:
            raise SandboxNotFoundError(f"Sandbox '{sandbox_id}' not found")
        return ref

    async def create_sandbox(
        self,
        spec: SandboxSpec,
        policy_data: SecurityPolicy | Mapping[str, object] | None = None,
        policy_mode: PolicyMode = PolicyMode.OVERRIDE,
    ) -> SandboxRef:
        """Create a new sandbox."""
        async with self._lock:
            sandbox_id = str(uuid.uuid4())[:12]
            policy = self._resolve_effective_policy(policy_data, policy_mode)
            logger.debug("Creating sandbox %s with policy %s", sandbox_id, str(policy))
            self.policy_engine.validate_policy(policy)
            # Create sandbox ref
            ref = SandboxRef(
                id=sandbox_id,
                phase=SandboxPhase.PROVISIONING,
                env=dict(spec.env),
            )
            self._sandboxes[sandbox_id] = ref
            self._policies[sandbox_id] = policy
            self._save_state(ref)

            self.audit.log(AuditEventType.SANDBOX_CREATED, sandbox_id)

            # Write resolved policy
            policy_path = self.policy_engine.write_sandbox_policy(sandbox_id, policy)
            self.audit.log(AuditEventType.POLICY_APPLIED, sandbox_id, policy_name=policy.name)

        # Runtime startup can be expensive. Do it outside the manager-wide lock
        # so independent sandboxes can start in parallel.
        try:
            pid = await self.runtime.create(
                sandbox_id=sandbox_id,
                policy_path=policy_path,
                workdir=None,
                env=ref.env,
            )
            cleanup_after_create = False
            async with self._lock:
                current_ref = self._sandboxes.get(sandbox_id)
                if current_ref is not ref or ref.phase == SandboxPhase.DELETING:
                    cleanup_after_create = True
                else:
                    ref.phase = SandboxPhase.READY
                    ref.pid = pid
                    ref.started_at = datetime.now(timezone.utc)
                    self._save_state(ref)
            if cleanup_after_create:
                await self.runtime.cleanup(sandbox_id)
        except Exception as e:
            async with self._lock:
                current_ref = self._sandboxes.get(sandbox_id)
                if current_ref is not ref or ref.phase == SandboxPhase.DELETING:
                    return ref
                ref.phase = SandboxPhase.ERROR
                ref.error_message = str(e)
                logger.error("Failed to create sandbox %s: %s", sandbox_id, e)
                self._save_state(ref)

        return ref

    async def get_sandbox(self, sandbox_id: str) -> SandboxRef:
        async with self._lock:
            ref = self._get_sandbox(sandbox_id)
            # Refresh running status
            if ref.phase == SandboxPhase.READY:
                if not await self.runtime.is_running(sandbox_id):
                    ref.phase = SandboxPhase.STOPPED
                    diagnostics = getattr(self.runtime, "get_exit_diagnostics", None)
                    if diagnostics is not None:
                        ref.error_message = diagnostics(sandbox_id)
                    self._save_state(ref)
            return ref

    async def list_sandboxes(self) -> list[SandboxRef]:
        async with self._lock:
            return list(self._sandboxes.values())

    async def start_sandbox(self, sandbox_id: str) -> SandboxRef:
        async with self._lock:
            return await self._start_sandbox_unlocked(sandbox_id)

    async def _start_sandbox_unlocked(self, sandbox_id: str) -> SandboxRef:
        ref = self._get_sandbox(sandbox_id)
        if ref.phase == SandboxPhase.READY:
            if await self.runtime.is_running(sandbox_id):
                return ref

        policy = self._policies.get(sandbox_id)
        if policy is None:
            policy_path = self.policy_engine.get_sandbox_policy_path(sandbox_id)
            if policy_path:
                policy = self.policy_engine.load_policy_from_file(policy_path)
            else:
                raise SandboxStateError(f"No policy found for sandbox {sandbox_id}")

        policy_path = self.policy_engine.get_sandbox_policy_path(sandbox_id)
        if policy_path is None:
            policy_path = self.policy_engine.write_sandbox_policy(sandbox_id, policy)

        try:
            pid = await self.runtime.create(
                sandbox_id=sandbox_id,
                policy_path=policy_path,
                workdir=None,
                env=ref.env,
            )
            ref.phase = SandboxPhase.READY
            ref.pid = pid
            ref.started_at = datetime.now(timezone.utc)
            ref.error_message = None
        except Exception as e:
            logger.error("Failed to start sandbox %s: %s", sandbox_id, e, exc_info=True)
            ref.phase = SandboxPhase.ERROR
            ref.error_message = str(e)

        self._save_state(ref)
        self.audit.log(AuditEventType.SANDBOX_STARTED, sandbox_id)
        return ref

    async def stop_sandbox(self, sandbox_id: str) -> SandboxRef:
        async with self._lock:
            return await self._stop_sandbox_unlocked(sandbox_id)

    async def _stop_sandbox_unlocked(self, sandbox_id: str) -> SandboxRef:
        ref = self._get_sandbox(sandbox_id)
        await self.runtime.stop(sandbox_id)
        ref.phase = SandboxPhase.STOPPED
        ref.pid = None
        self._save_state(ref)
        self.audit.log(AuditEventType.SANDBOX_STOPPED, sandbox_id)
        return ref

    async def restart_sandbox(self, sandbox_id: str) -> SandboxRef:
        async with self._lock:
            await self._stop_sandbox_unlocked(sandbox_id)
            return await self._start_sandbox_unlocked(sandbox_id)

    async def delete_sandbox(self, sandbox_id: str) -> None:
        async with self._lock:
            ref = self._get_sandbox(sandbox_id)
            ref.phase = SandboxPhase.DELETING
            self._save_state(ref)

        # Cleanup can wait on processes and namespace teardown. Keep it outside
        # the global state lock so deleting one sandbox does not block unrelated
        # sandbox operations.
        await self.runtime.cleanup(sandbox_id)
        self.policy_engine.delete_sandbox_policy(sandbox_id)
        self.audit.log(AuditEventType.SANDBOX_DELETED, sandbox_id)

        async with self._lock:
            self._sandboxes.pop(sandbox_id, None)
            self._policies.pop(sandbox_id, None)
            self._delete_state(sandbox_id)

    async def exec_in_sandbox(
        self,
        sandbox_id: str,
        request: SandboxExecRequest,
    ) -> ExecResult:
        async with self._lock:
            ref = self._get_sandbox(sandbox_id)
            if ref.phase != SandboxPhase.READY:
                raise SandboxStateError(
                    f"Cannot exec in sandbox '{sandbox_id}': state is {ref.phase.value}"
                )

        # One audit row per exec, emitted **after** the runtime returns so
        # the payload covers both intent (command/workdir) and outcome
        # (exit_code, stdout/stderr tail, duration, error). The earlier
        # pre-call ``EXEC_COMMAND`` was dropped: it doubled the JSONL
        # volume without adding any information not already present here.
        start = time.monotonic()
        try:
            result = await self.runtime.exec(
                sandbox_id,
                RuntimeExecRequest(
                    command=request.command,
                    workdir=request.workdir,
                    env=request.env,
                    stdin_data=request.stdin_data,
                    timeout=request.timeout,
                ),
            )
        except Exception as exc:
            self.audit.log(
                AuditEventType.EXEC_COMMAND,
                sandbox_id,
                command=request.command,
                workdir=request.workdir,
                ok=False,
                error=repr(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
            raise
        self.audit.log(
            AuditEventType.EXEC_COMMAND,
            sandbox_id,
            command=request.command,
            workdir=request.workdir,
            ok=result.exit_code == 0,
            exit_code=result.exit_code,
            stdout=_truncate_for_audit(result.stdout),
            stderr=_truncate_for_audit(result.stderr),
            duration_ms=int((time.monotonic() - start) * 1000),
        )
        return result

    async def exec_background_in_sandbox(
        self,
        sandbox_id: str,
        request: SandboxExecRequest,
    ) -> BackgroundExecResult:
        async with self._lock:
            ref = self._get_sandbox(sandbox_id)
            if ref.phase != SandboxPhase.READY:
                raise SandboxStateError(
                    f"Cannot exec in sandbox '{sandbox_id}': state is {ref.phase.value}"
                )

        # Background exec returns a "started yes/no + pid" envelope
        # rather than stdout/stderr — the raw byte stream is dropped
        # at the kernel level (runtime.log was removed). One audit row
        # per call, post-return.
        start = time.monotonic()
        try:
            result = await self.runtime.exec_background(
                sandbox_id,
                RuntimeExecRequest(
                    command=request.command,
                    workdir=request.workdir,
                    env=request.env,
                    stdin_data=request.stdin_data,
                    timeout=request.timeout,
                ),
            )
        except Exception as exc:
            self.audit.log(
                AuditEventType.EXEC_COMMAND,
                sandbox_id,
                command=request.command,
                workdir=request.workdir,
                background=True,
                ok=False,
                error=repr(exc),
                duration_ms=int((time.monotonic() - start) * 1000),
            )
            raise
        self.audit.log(
            AuditEventType.EXEC_COMMAND,
            sandbox_id,
            command=request.command,
            workdir=request.workdir,
            background=True,
            ok=result.started,
            started=result.started,
            pid=result.pid,
            error=result.error_message,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
        return result

    async def upload_file_to_sandbox(
        self,
        sandbox_id: str,
        sandbox_path: str,
        content: bytes,
    ) -> None:
        async with self._lock:
            ref = self._get_sandbox(sandbox_id)
            if ref.phase != SandboxPhase.READY:
                raise SandboxStateError(
                    f"Cannot upload to sandbox '{sandbox_id}': state is {ref.phase.value}"
                )

        # One audit row per upload, emitted after the call returns so the
        # payload covers both intent (path/size) and outcome (ok, error,
        # which transport landed it). The earlier pre-call event was
        # dropped (see ``exec_in_sandbox`` for the same rationale).
        start = time.monotonic()

        def _emit_result(ok: bool, **extra) -> None:
            self.audit.log(
                AuditEventType.FILE_TRANSFER,
                sandbox_id,
                direction="upload",
                sandbox_path=sandbox_path,
                size=len(content),
                ok=ok,
                duration_ms=int((time.monotonic() - start) * 1000),
                **extra,
            )

        # Fast path: tell the in-sandbox daemon to write the file in its
        # own process. The daemon already runs with the sandbox uid/gid,
        # mount layout, seccomp filter, and Landlock ruleset, so doing
        # the write in-process is exactly equivalent (security-wise) to
        # spawning ``bash -c 'cat > "$target"'`` but skips the bash
        # cold-start and an extra fork/exec roundtrip per upload.
        try:
            result = await self.runtime.write_file(
                sandbox_id,
                sandbox_path,
                content,
                mkdir_parents=True,
            )
        except Exception as exc:
            _emit_result(False, error=repr(exc), path="ipc")
            raise
        if result.ok:
            _emit_result(True, path="ipc")
            return

        if result.error in ("daemon_unavailable", "transport_failure", "unsupported"):
            # Fallback path runs ``exec_in_sandbox`` internally which
            # itself emits one ``EXEC_COMMAND`` row for the bash+cat
            # invocation; we still emit ``FILE_TRANSFER`` here so the
            # per-transfer summary is one greppable line regardless of
            # whether IPC or the fallback ran.
            try:
                await self._upload_via_exec_fallback(sandbox_id, sandbox_path, content)
            except Exception as exc:
                _emit_result(False, error=repr(exc), path="exec_fallback")
                raise
            _emit_result(True, path="exec_fallback")
            return

        detail = result.detail or result.error or "unknown failure"
        _emit_result(False, error=detail, path="ipc")
        raise SandboxStateError(
            f"Failed to upload file to '{sandbox_path}': {detail}"
        )

    async def _upload_via_exec_fallback(
        self,
        sandbox_id: str,
        sandbox_path: str,
        content: bytes,
    ) -> None:
        """Legacy ``bash + cat`` upload path used only when the IPC fast
        path is unavailable (e.g. an older runtime adapter, or a sandbox
        whose daemon flagged itself unhealthy mid-request)."""
        upload_script = textwrap.dedent(
            """
            set -euo pipefail
            target="$1"
            parent=$(dirname -- "$target") || {
                status=$?
                printf "dirname failed for upload target '%s' (exit %s)\\n" "$target" "$status" >&2
                exit "$status"
            }
            mkdir -p -- "$parent" || {
                status=$?
                uid=$(id -u 2>/dev/null || true)
                gid=$(id -g 2>/dev/null || true)
                parent_parent=$(dirname -- "$parent" 2>/dev/null || true)
                printf "mkdir failed: parent='%s' target='%s'\\n" "$parent" "$target" >&2
                printf "sandbox identity: uid=%s gid=%s exit=%s\\n" "$uid" "$gid" "$status" >&2
                if [ -n "$parent_parent" ]; then
                    ls -ld -- "$parent_parent" "$parent" >&2 || true
                fi
                exit "$status"
            }
            cat > "$target" || {
                status=$?
                printf "write failed: target='%s' exit=%s\\n" "$target" "$status" >&2
                exit "$status"
            }
            """
        ).strip()
        result = await self.exec_in_sandbox(
            sandbox_id,
            SandboxExecRequest(
                command=[
                    "bash",
                    "-c",
                    upload_script,
                    "jiuwenbox-upload",
                    sandbox_path,
                ],
                stdin_data=content,
            ),
        )
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout).strip()
            if not detail:
                detail = f"command exited with code {result.exit_code} without stderr/stdout"
            raise SandboxStateError(
                f"Failed to upload file to '{sandbox_path}': {detail}"
            )

    async def download_file_from_sandbox(
        self,
        sandbox_id: str,
        sandbox_path: str,
    ) -> bytes:
        async with self._lock:
            ref = self._get_sandbox(sandbox_id)
            if ref.phase != SandboxPhase.READY:
                raise SandboxStateError(
                    f"Cannot download from sandbox '{sandbox_id}': state is {ref.phase.value}"
                )

        # Mirror of ``upload_file_to_sandbox``: a single post-result row
        # carrying intent + outcome. ``size`` is filled in on success
        # (from the actual bytes returned), 0 otherwise.
        start = time.monotonic()

        def _emit_result(ok: bool, size: int = 0, **extra) -> None:
            self.audit.log(
                AuditEventType.FILE_TRANSFER,
                sandbox_id,
                direction="download",
                sandbox_path=sandbox_path,
                size=size,
                ok=ok,
                duration_ms=int((time.monotonic() - start) * 1000),
                **extra,
            )

        # Fast path: ask the daemon to read the file directly. The daemon
        # carries the sandbox's full security envelope so it cannot read
        # any path that user code couldn't read. Binary content survives
        # the IPC unchanged - no base64 round-trip.
        try:
            result = await self.runtime.read_file(sandbox_id, sandbox_path)
        except Exception as exc:
            _emit_result(False, error=repr(exc), path="ipc")
            raise
        if result.ok:
            content = result.content or b""
            _emit_result(True, size=len(content), path="ipc")
            return content

        if result.error == "not_found":
            _emit_result(False, error="not_found", path="ipc")
            raise FileNotFoundError(sandbox_path)
        if result.error in ("is_directory", "is_a_directory"):
            _emit_result(False, error=result.error, path="ipc")
            raise SandboxStateError(f"Sandbox path '{sandbox_path}' is a directory")
        if result.error == "is_symlink":
            _emit_result(False, error="is_symlink", path="ipc")
            raise SandboxStateError(
                f"Refusing to follow symlink at '{sandbox_path}'"
            )
        if result.error in ("daemon_unavailable", "transport_failure", "unsupported"):
            # Fallback path emits its own ``EXEC_COMMAND`` row for the
            # bash+base64 invocation; we still emit ``FILE_TRANSFER`` so
            # the per-transfer summary is one greppable line regardless
            # of which transport landed it.
            try:
                content = await self._download_via_exec_fallback(sandbox_id, sandbox_path)
            except Exception as exc:
                _emit_result(False, error=repr(exc), path="exec_fallback")
                raise
            _emit_result(True, size=len(content), path="exec_fallback")
            return content

        detail = result.detail or result.error or "unknown failure"
        _emit_result(False, error=detail, path="ipc")
        raise SandboxStateError(
            f"Failed to download file from '{sandbox_path}': {detail}"
        )

    async def _download_via_exec_fallback(
        self,
        sandbox_id: str,
        sandbox_path: str,
    ) -> bytes:
        """Legacy bash+base64 download path used only when the IPC fast
        path is unavailable."""
        result = await self.exec_in_sandbox(
            sandbox_id,
            SandboxExecRequest(
                command=[
                    "bash",
                    "-c",
                    (
                        "set -euo pipefail; "
                        'target="$1"; '
                        'if [ ! -e "$target" ]; then exit 44; fi; '
                        'if [ -d "$target" ]; then exit 45; fi; '
                        'base64 -w 0 -- "$target"'
                    ),
                    "jiuwenbox-download",
                    sandbox_path,
                ],
            ),
        )
        if result.exit_code == 44:
            raise FileNotFoundError(sandbox_path)
        if result.exit_code == 45:
            raise SandboxStateError(f"Sandbox path '{sandbox_path}' is a directory")
        if result.exit_code != 0:
            raise SandboxStateError(
                f"Failed to download file from '{sandbox_path}': {result.stderr or result.stdout}"
            )

        try:
            return base64.b64decode(result.stdout.encode(), validate=True)
        except binascii.Error as exc:
            raise SandboxStateError(
                f"Failed to decode downloaded file from '{sandbox_path}'"
            ) from exc

    async def list_files_in_sandbox(
        self,
        sandbox_id: str,
        request: SandboxListRequest,
    ) -> list[dict[str, object]]:
        async with self._lock:
            ref = self._get_sandbox(sandbox_id)
            if ref.phase != SandboxPhase.READY:
                raise SandboxStateError(
                    f"Cannot list files in sandbox '{sandbox_id}': state is {ref.phase.value}"
                )

        # Fast path: ask the daemon to walk the directory in-process.
        # Saves the python3 cold start and the fork+exec that the legacy
        # helper paid on every call.
        result = await self.runtime.list_dir(
            sandbox_id,
            request.sandbox_path,
            recursive=request.recursive,
            max_depth=request.max_depth,
            include_files=request.include_files,
            include_dirs=request.include_dirs,
        )
        if result.ok:
            return list(result.items or [])

        if result.error == "not_found":
            raise FileNotFoundError(request.sandbox_path)
        if result.error in ("not_a_directory", "is_not_a_directory"):
            raise SandboxStateError(
                f"Sandbox path '{request.sandbox_path}' is not a directory"
            )
        if result.error in ("daemon_unavailable", "transport_failure", "unsupported"):
            return await self._list_via_exec_fallback(sandbox_id, request)

        detail = result.detail or result.error or "unknown failure"
        raise SandboxStateError(
            f"Failed to list files in '{request.sandbox_path}': {detail}"
        )

    async def _list_via_exec_fallback(
        self,
        sandbox_id: str,
        request: SandboxListRequest,
    ) -> list[dict[str, object]]:
        """Legacy ``python3`` helper kept for runtimes without a daemon
        IPC channel."""
        script = textwrap.dedent(
            """
            import datetime
            import json
            import os
            from pathlib import Path
            import sys

            root = Path(sys.argv[1])
            recursive = sys.argv[2] == "1"
            max_depth = None if sys.argv[3] == "" else int(sys.argv[3])
            include_files = sys.argv[4] == "1"
            include_dirs = sys.argv[5] == "1"

            if not root.exists():
                sys.exit(44)
            if not root.is_dir():
                sys.exit(45)

            if recursive:
                entries = root.rglob("*")
            else:
                entries = root.iterdir()

            items = []
            for entry in entries:
                try:
                    stat = entry.stat()
                except OSError:
                    continue

                rel_parts = entry.relative_to(root).parts
                if max_depth is not None and len(rel_parts) > max_depth:
                    continue

                is_dir = entry.is_dir()
                if is_dir and not include_dirs:
                    continue
                if not is_dir and not include_files:
                    continue

                items.append({
                    "name": entry.name,
                    "path": str(entry),
                    "size": 0 if is_dir else stat.st_size,
                    "is_directory": is_dir,
                    "modified_time": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "type": None if is_dir else os.path.splitext(entry.name)[1] or None,
                })

            items.sort(key=lambda item: item["path"])
            print(json.dumps(items, ensure_ascii=False))
            """
        ).strip()

        result = await self.exec_in_sandbox(
            sandbox_id,
            SandboxExecRequest(
                command=[
                    "python3",
                    "-S",
                    "-c",
                    script,
                    request.sandbox_path,
                    "1" if request.recursive else "0",
                    "" if request.max_depth is None else str(request.max_depth),
                    "1" if request.include_files else "0",
                    "1" if request.include_dirs else "0",
                ],
            ),
        )
        if result.exit_code == 44:
            raise FileNotFoundError(request.sandbox_path)
        if result.exit_code == 45:
            raise SandboxStateError(
                f"Sandbox path '{request.sandbox_path}' is not a directory"
            )
        if result.exit_code != 0:
            raise SandboxStateError(
                f"Failed to list files in '{request.sandbox_path}': {result.stderr or result.stdout}"
            )
        return json.loads(result.stdout or "[]")

    async def search_files_in_sandbox(
        self,
        sandbox_id: str,
        sandbox_path: str,
        pattern: str,
        exclude_patterns: list[str] | None = None,
    ) -> list[dict[str, object]]:
        script = textwrap.dedent(
            """
            import datetime
            import fnmatch
            import json
            import os
            from pathlib import Path
            import sys

            root = Path(sys.argv[1])
            pattern = sys.argv[2]
            exclude_patterns = json.loads(sys.argv[3])

            if not root.exists():
                sys.exit(44)
            if not root.is_dir():
                sys.exit(45)

            items = []
            for entry in root.rglob("*"):
                if not entry.is_file():
                    continue
                rel = str(entry.relative_to(root))
                if not (fnmatch.fnmatch(entry.name, pattern) or fnmatch.fnmatch(rel, pattern)):
                    continue
                if any(fnmatch.fnmatch(entry.name, item) or fnmatch.fnmatch(rel, item) for item in exclude_patterns):
                    continue

                try:
                    stat = entry.stat()
                except OSError:
                    continue

                items.append({
                    "name": entry.name,
                    "path": str(entry),
                    "size": stat.st_size,
                    "is_directory": False,
                    "modified_time": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "type": os.path.splitext(entry.name)[1] or None,
                })

            items.sort(key=lambda item: item["path"])
            print(json.dumps(items, ensure_ascii=False))
            """
        ).strip()

        result = await self.exec_in_sandbox(
            sandbox_id,
            SandboxExecRequest(
                # ``-S`` skips ``import site`` for the in-sandbox python3 cold
                # start; the helper script only needs the standard library.
                command=[
                    "python3",
                    "-S",
                    "-c",
                    script,
                    sandbox_path,
                    pattern,
                    json.dumps(exclude_patterns or []),
                ],
            ),
        )
        if result.exit_code == 44:
            raise FileNotFoundError(sandbox_path)
        if result.exit_code == 45:
            raise SandboxStateError(f"Sandbox path '{sandbox_path}' is not a directory")
        if result.exit_code != 0:
            raise SandboxStateError(
                f"Failed to search files in '{sandbox_path}': {result.stderr or result.stdout}"
            )
        return json.loads(result.stdout or "[]")

    async def get_logs(self, sandbox_id: str) -> str:
        async with self._lock:
            self._get_sandbox(sandbox_id)
            return self.audit.read_logs_raw(sandbox_id)

    async def get_policy(self, sandbox_id: str) -> SecurityPolicy | None:
        async with self._lock:
            policy = self._policies.get(sandbox_id)
            if policy is not None:
                return policy

            if self._sandboxes.get(sandbox_id) is None:
                return None

            policy_path = self.policy_engine.get_sandbox_policy_path(sandbox_id)
            if policy_path is None:
                return None

            policy = self.policy_engine.load_policy_from_file(policy_path)
            self._policies[sandbox_id] = policy
            return policy
