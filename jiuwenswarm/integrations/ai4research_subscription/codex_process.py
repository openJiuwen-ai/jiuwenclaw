"""Hardened one-process-per-turn Codex execution."""

from __future__ import annotations

import asyncio
import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .codex_binary import resolve_codex_binary, verify_codex_version
from .codex_jsonl import parse_codex_jsonl
from .constants import (
    DEFAULT_TURN_TIMEOUT_SECONDS,
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
    PROCESS_TERMINATE_GRACE_SECONDS,
)
from .contracts import ProviderTurnResult, build_output_schema, build_provider_prompt
from .errors import CodexProviderError
from .locking import acquire_profile_lock_async, release_profile_lock
from .process_lifecycle import (
    await_task_uninterruptibly,
    read_limited,
    spawn_owned_subprocess,
    terminate_process_group,
    wait_process_exit,
)
from .profiles import CodexProfile, build_codex_environment, ensure_codex_profile, verify_codex_auth_file
from .quarantine import (
    profile_is_quarantined,
    quarantine_ownership,
    reconcile_profile_quarantine,
)
from .turn_directory import cleanup_owned_turn_directory


_DISABLED_PROVIDER_TOOL_FEATURES = (
    "shell_tool",
    "unified_exec",
    "code_mode_host",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "apps",
    "image_generation",
    "multi_agent",
    "plugins",
)

_PROVIDER_CONFIG_OVERRIDES = (
    'forced_login_method="chatgpt"',
    'cli_auth_credentials_store="file"',
    'approval_policy="never"',
    'default_permissions="ai4research_provider"',
    'permissions.ai4research_provider.filesystem={":minimal"="read"}',
    "permissions.ai4research_provider.network.enabled=false",
    'web_search="disabled"',
    'shell_environment_policy.inherit="none"',
    "mcp_servers={}",
)


_EVIDENCE_FIELDS = frozenset(
    {
        "event",
        "timestamp_monotonic",
        "pid",
        "ppid",
        "pgid",
        "sid",
        "state",
        "etimes",
        "start_ticks",
        "group_empty",
        "live_group_empty",
        "zombie_count",
        "turn_empty",
        "lock_available",
        "reader_tasks_done",
        "cleanup_complete",
        "cleanup_elapsed_seconds",
        "cleanup_deadline_seconds",
        "process_scan_count",
        "quarantined",
    }
)


class CodexProcessRunner:
    """Run the pinned Codex CLI with no repository or provider-owned tools."""

    def __init__(self, *, binary_path: Path | None = None, enforce_version: bool = True):
        self._binary_path = binary_path
        self._enforce_version = enforce_version
        self._lifecycle_evidence: list[dict[str, Any]] = []

    @property
    def lifecycle_evidence(self) -> tuple[dict[str, Any], ...]:
        """Secret-free process lifecycle evidence for tests and protected gates."""
        return tuple(dict(item) for item in self._lifecycle_evidence)

    def _observe(self, event: str, **fields: Any) -> None:
        evidence = {
            "event": event,
            "timestamp_monotonic": time.monotonic(),
            **fields,
        }
        unexpected = evidence.keys() - _EVIDENCE_FIELDS
        if unexpected:
            raise ValueError(f"Unsupported lifecycle evidence field(s): {sorted(unexpected)}")
        self._lifecycle_evidence.append(evidence)

    async def _verified(self, profile: CodexProfile, temporary_dir: Path) -> Path:
        binary = resolve_codex_binary(self._binary_path)
        environment = build_codex_environment(profile, binary=binary, temporary_dir=temporary_dir)
        if self._enforce_version:
            await verify_codex_version(binary, environment, profile)
        return binary

    async def run(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        timeout: float | None = None,
    ) -> ProviderTurnResult:
        try:
            turn_timeout = (
                DEFAULT_TURN_TIMEOUT_SECONDS if timeout is None else float(timeout)
            )
        except (TypeError, ValueError) as exc:
            raise CodexProviderError(
                "invalid_request", "The Codex turn timeout must be a positive number."
            ) from exc
        if not math.isfinite(turn_timeout) or turn_timeout <= 0:
            raise CodexProviderError(
                "invalid_request", "The Codex turn timeout must be a positive number."
            )
        self._lifecycle_evidence = []
        profile = ensure_codex_profile()
        await reconcile_profile_quarantine(profile)
        lock_handle = await acquire_profile_lock_async(profile)
        self._observe("lock_acquired", lock_available=False)
        turn_dir: Path | None = None
        process: asyncio.subprocess.Process | None = None
        process_group_id: int | None = None
        wait_task: asyncio.Task[int] | None = None
        reader_tasks: tuple[asyncio.Task[bytes], ...] = ()
        result_data: tuple[int, bytes] | None = None
        tool_names: list[str] = []
        pending_error: BaseException | None = None
        try:
            verify_codex_auth_file(profile)
            turn_dir = Path(
                tempfile.mkdtemp(prefix="turn-", dir=profile.turns_dir)
            )
            try:
                os.chmod(turn_dir, 0o700)
                binary = await self._verified(profile, turn_dir)
                tool_names = [tool["function"]["name"] for tool in tools]
                prompt = build_provider_prompt(messages, tools)
                schema_path = turn_dir / "response-schema.json"
                schema_path.write_text(
                    json.dumps(build_output_schema(tool_names), separators=(",", ":")),
                    encoding="utf-8",
                )
                os.chmod(schema_path, 0o600)
                environment = build_codex_environment(profile, binary=binary, temporary_dir=turn_dir)
                argv = [
                    str(binary),
                    "exec",
                    "--strict-config",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--skip-git-repo-check",
                    "--ephemeral",
                    "--color",
                    "never",
                    "--json",
                    "--output-schema",
                    str(schema_path),
                    "--cd",
                    str(turn_dir),
                ]
                for feature in _DISABLED_PROVIDER_TOOL_FEATURES:
                    argv.extend(("--disable", feature))
                for override in _PROVIDER_CONFIG_OVERRIDES:
                    argv.extend(("-c", override))
                argv.append("-")
                process, spawn_cancellation = await spawn_owned_subprocess(
                    *argv,
                    task_name="codex-model-spawn",
                    cwd=turn_dir,
                    env=environment,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=os.name == "posix",
                )
                if os.name == "posix":
                    process_group_id = process.pid
                self._observe(
                    "process_started",
                    pid=process.pid,
                    pgid=process_group_id,
                    group_empty=False,
                )
                if spawn_cancellation is not None:
                    raise spawn_cancellation
                stdin_writer = process.stdin
                stdout_reader = process.stdout
                stderr_reader = process.stderr
                if (
                    stdin_writer is None
                    or stdout_reader is None
                    or stderr_reader is None
                ):
                    raise CodexProviderError(
                        "provider_failed", "Codex could not complete the model turn."
                    )
                wait_task = asyncio.create_task(wait_process_exit(process))
                stdout_task = asyncio.create_task(
                    read_limited(stdout_reader, MAX_STDOUT_BYTES)
                )
                stderr_task = asyncio.create_task(
                    read_limited(stderr_reader, MAX_STDERR_BYTES)
                )
                reader_tasks = (stdout_task, stderr_task)
                async with asyncio.timeout(turn_timeout):
                    stdin_writer.write(prompt.encode("utf-8"))
                    await stdin_writer.drain()
                    stdin_writer.close()
                    await stdin_writer.wait_closed()
                    returncode, stdout, _stderr = await asyncio.gather(
                        wait_task, stdout_task, stderr_task
                    )
                result_data = (returncode, stdout)
            except TimeoutError as exc:
                pending_error = CodexProviderError(
                    "timeout", "Codex exceeded the configured turn timeout."
                )
                pending_error.__cause__ = exc
            except asyncio.CancelledError as exc:
                pending_error = exc
            except CodexProviderError as exc:
                pending_error = exc
            except Exception as exc:
                pending_error = CodexProviderError(
                    "provider_failed", "Codex could not complete the model turn."
                )
                pending_error.__cause__ = exc
        except asyncio.CancelledError as exc:
            pending_error = exc
        except CodexProviderError as exc:
            pending_error = exc
        except Exception as exc:
            pending_error = CodexProviderError(
                "provider_failed", "Codex could not complete the model turn."
            )
            pending_error.__cause__ = exc

        async def finalize() -> None:
            process_tree_error: BaseException | None = None
            turn_directory_error: BaseException | None = None
            lock_error: BaseException | None = None
            if process is not None:
                try:
                    await terminate_process_group(
                        process,
                        process_group_id,
                        self._observe,
                        grace_seconds=PROCESS_TERMINATE_GRACE_SECONDS,
                    )
                except BaseException as exc:  # cleanup must continue through every owned resource
                    process_tree_error = exc
            for task in reader_tasks:
                if not task.done():
                    task.cancel()
            if reader_tasks:
                await asyncio.gather(*reader_tasks, return_exceptions=True)
            if wait_task is not None:
                if not wait_task.done():
                    wait_task.cancel()
                await asyncio.gather(wait_task, return_exceptions=True)
            self._observe(
                "readers_reaped",
                reader_tasks_done=all(task.done() for task in reader_tasks),
            )
            if process_tree_error is None and profile_is_quarantined(profile):
                process_tree_error = RuntimeError("A provider subprocess remains quarantined.")
            if process_tree_error is None and turn_dir is not None:
                try:
                    cleanup_owned_turn_directory(turn_dir, profile.turns_dir)
                except BaseException as exc:
                    turn_directory_error = exc
            try:
                self._observe(
                    "turn_removed", turn_empty=not any(profile.turns_dir.iterdir())
                )
            except BaseException as exc:
                turn_directory_error = turn_directory_error or exc
            if process_tree_error is not None or turn_directory_error is not None:
                try:
                    quarantine_ownership(
                        profile,
                        process=process if process_tree_error is not None else None,
                        pgid=process_group_id if process_tree_error is not None else None,
                        lock_handle=lock_handle,
                        turn_dir=turn_dir,
                    )
                finally:
                    self._observe("profile_quarantined", quarantined=True)
                    self._observe("cleanup_finished", cleanup_complete=False)
                raise CodexProviderError(
                    "provider_quarantined",
                    "Codex is unavailable until uncertain process ownership is safely reconciled.",
                ) from None
            try:
                release_profile_lock(lock_handle)
            except BaseException as exc:
                lock_error = exc
                try:
                    quarantine_ownership(
                        profile,
                        process=None,
                        pgid=None,
                        lock_handle=lock_handle,
                    )
                finally:
                    self._observe("profile_quarantined", quarantined=True)
            else:
                self._observe("lock_released", lock_available=True)
            self._observe("cleanup_finished", cleanup_complete=lock_error is None)
            if lock_error is not None:
                raise CodexProviderError(
                    "provider_quarantined",
                    "Codex is unavailable until uncertain process ownership is safely reconciled.",
                ) from None

        initial_cancellation = (
            pending_error if isinstance(pending_error, asyncio.CancelledError) else None
        )
        cleanup_task = asyncio.create_task(finalize(), name="codex-turn-finalizer")
        try:
            _, cleanup_cancellation = await await_task_uninterruptibly(
                cleanup_task,
                initial_cancellation,
            )
        except Exception as exc:
            pending_error = (
                exc
                if isinstance(exc, CodexProviderError)
                and exc.code == "provider_quarantined"
                else CodexProviderError(
                    "provider_failed", "Codex could not clean up the model turn."
                )
            )
            cleanup_cancellation = initial_cancellation

        if pending_error is not None:
            raise pending_error
        if cleanup_cancellation is not None:
            raise cleanup_cancellation
        if result_data is None:
            raise CodexProviderError(
                "provider_failed", "Codex could not complete the model turn."
            )
        returncode, stdout = result_data
        if returncode != 0:
            raise CodexProviderError("provider_failed", "Codex could not complete the model turn.")
        return parse_codex_jsonl(stdout, allowed_tool_names=set(tool_names))
