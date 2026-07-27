"""Hardened one-process-per-turn Claude CLI execution.

Reuses the provider-neutral process-lifecycle and turn-directory machinery.
This is a **subscription-only** provider. Each turn:

1. reconciles any prior quarantine (``reconcile_claude_quarantine``) and refuses
   to start until an earlier uncertain process group is proven gone;
2. runs ``claude auth status --json`` in the SAME restricted env used for
   inference and permits the turn ONLY when it positively proves a Claude
   subscription login - API-key, console/token, and cloud billing all fail
   closed (locked requirement; see ``claude_auth_status``);
3. runs one fresh, tool-disabled ``claude -p`` inference.

Both the preflight and the inference are fully owned subprocesses. Consequences
versus the Codex runner:

* no managed credential home, no ``auth.json`` verification, no profile lock -
  this product neither reads, writes, copies, nor manages the login credential;
* a workspace-private scratch directory holds only ephemeral per-turn working
  directories; it never holds credentials;
* the environment passed to the child is a strict allowlist. Because the real
  ``HOME`` is passed, the child *can read* the operator's ``~/.claude`` login -
  shared, mutable, operator-owned credential state that lives outside this
  product. The product does not isolate or protect it; it only enables the CLI's
  own native resolution and strips every unrelated and Jiuwen-internal variable.
  A child that leaked would retain that same read access, which is why a turn
  that cannot confirm its child group was reaped records the group in a strict
  cross-turn **quarantine** (``claude_quarantine``) and fails closed - and every
  later turn stays blocked until that group is proven gone.
"""

from __future__ import annotations

import asyncio
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from jiuwenswarm.common.utils import get_user_workspace_dir

from .claude_auth_seam import ClaudeProviderStatus, ClaudeSubscriptionAuthState
from .claude_auth_status import (
    AUTH_STATUS_ARGV_TAIL,
    MAX_AUTH_STATUS_BYTES,
    classify_subscription_auth,
)
from .claude_binary import resolve_claude_binary, verify_claude_version
from .claude_constants import MAX_CLAUDE_STDERR_BYTES, MAX_CLAUDE_STDOUT_BYTES
from .claude_contracts import ProviderTurnResult, build_claude_prompt
from .claude_output import parse_claude_result
from .claude_quarantine import reconcile_claude_quarantine, record_uncertain_groups
from .errors import (
    ClaudeProviderError,
    claude_auth_unverifiable,
    claude_login_required,
    claude_provider_unavailable,
    claude_wrong_auth_method,
)
from .constants import DEFAULT_TURN_TIMEOUT_SECONDS, PROCESS_TERMINATE_GRACE_SECONDS
from .process_lifecycle import (
    await_task_uninterruptibly,
    read_limited,
    spawn_owned_subprocess,
    terminate_process_group,
    wait_process_exit,
)
from .turn_directory import cleanup_owned_turn_directory

# Bounded ceiling for the auth-status preflight probe. It is a fast, local-ish
# check; cap it well under a full turn so a stuck probe fails closed quickly.
_AUTH_STATUS_TIMEOUT_CEILING_SECONDS = 45.0

# Environment variables copied from the operator's environment when present, so
# the CLI's own login resolution works. This is an allowlist, not inheritance:
# nothing outside this set (no Jiuwen-internal secrets) is passed.
#
# Subscription-login-only: NO API-key variables are passed. ``ANTHROPIC_API_KEY``,
# ``ANTHROPIC_AUTH_TOKEN``, and ``ANTHROPIC_BASE_URL`` are deliberately withheld
# so the child can authenticate only through the operator's ``claude`` login
# (resolved from the real ``HOME``/``~/.claude``), never an API key or a custom
# API endpoint. ``CLAUDE_CONFIG_DIR`` is honored only so a non-default login
# config location still resolves the operator's own login.
_NATIVE_CREDENTIAL_PASSTHROUGH = ("CLAUDE_CONFIG_DIR",)


@dataclass(frozen=True)
class ClaudeRuntime:
    """Credential-free workspace-private scratch area for Claude turns."""

    root: Path
    turns_dir: Path


def ensure_claude_runtime() -> ClaudeRuntime:
    if os.name == "nt":
        raise ClaudeProviderError(
            "unsupported_platform",
            "The Claude provider is not supported on Windows in this release.",
        )
    workspace = get_user_workspace_dir().expanduser().resolve(strict=False)
    if not workspace.exists():
        raise ClaudeProviderError("unsafe_runtime", "The Jiuwen instance workspace does not exist.")
    root = workspace / "private" / "subscription-providers" / "claude"
    turns_dir = root / "turns"
    for component in (
        workspace / "private",
        workspace / "private" / "subscription-providers",
        root,
        turns_dir,
    ):
        component.mkdir(mode=0o700, parents=False, exist_ok=True)
        os.chmod(component, 0o700)
    return ClaudeRuntime(root=root, turns_dir=turns_dir)


def build_claude_environment(*, binary: Path, turn_dir: Path) -> dict[str, str]:
    """Allowlisted environment that enables native credential resolution."""

    home = os.environ.get("HOME")
    if not home:
        raise ClaudeProviderError("unsafe_runtime", "No HOME is available for Claude credential resolution.")
    path_value = os.pathsep.join(dict.fromkeys((str(binary.parent), os.defpath)))
    environment = {
        "PATH": path_value,
        "HOME": home,
        "TMPDIR": str(turn_dir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_COLOR": "1",
    }
    for name in _NATIVE_CREDENTIAL_PASSTHROUGH:
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


class ClaudeProcessRunner:
    """Run the pinned Claude CLI once per turn with all provider tools disabled."""

    def __init__(self, *, binary_path: Path | None = None, enforce_version: bool = True):
        self._binary_path = binary_path
        self._enforce_version = enforce_version

    @staticmethod
    def _argv(binary: Path) -> list[str]:
        # Flags verified against 2.1.218 in Phase 0. Prompt arrives on stdin.
        # --tools "" disables all built-in tools; --setting-sources "" prevents
        # any repository/user settings, CLAUDE.md, hooks, or skills from shaping
        # the turn; --strict-mcp-config + no MCP config means no MCP servers;
        # --no-session-persistence keeps the turn stateless. No --model is
        # passed in V1 (CLI default); the provider alias is Jiuwen's selection
        # key, not a CLI model id.
        return [
            str(binary),
            "-p",
            "--output-format",
            "json",
            "--tools",
            "",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--no-session-persistence",
        ]

    @staticmethod
    def _resolve_timeout(timeout: float | None) -> float:
        try:
            turn_timeout = DEFAULT_TURN_TIMEOUT_SECONDS if timeout is None else float(timeout)
        except (TypeError, ValueError) as exc:
            raise ClaudeProviderError(
                "invalid_request", "The Claude turn timeout must be a positive number."
            ) from exc
        if not math.isfinite(turn_timeout) or turn_timeout <= 0:
            raise ClaudeProviderError(
                "invalid_request", "The Claude turn timeout must be a positive number."
            )
        return turn_timeout

    @staticmethod
    def _auth_state_error(state: ClaudeSubscriptionAuthState) -> ClaudeProviderError:
        if state is ClaudeSubscriptionAuthState.LOGIN_REQUIRED:
            return claude_login_required()
        if state is ClaudeSubscriptionAuthState.WRONG_AUTH_METHOD:
            return claude_wrong_auth_method()
        return claude_auth_unverifiable()

    @staticmethod
    def _auth_state_to_status(state: ClaudeSubscriptionAuthState) -> ClaudeProviderStatus:
        return {
            ClaudeSubscriptionAuthState.SUBSCRIPTION_READY: ClaudeProviderStatus.SUBSCRIPTION_READY,
            ClaudeSubscriptionAuthState.LOGIN_REQUIRED: ClaudeProviderStatus.LOGIN_REQUIRED,
            ClaudeSubscriptionAuthState.WRONG_AUTH_METHOD: ClaudeProviderStatus.WRONG_AUTH_METHOD,
            ClaudeSubscriptionAuthState.AUTH_STATUS_UNVERIFIABLE: ClaudeProviderStatus.AUTH_STATUS_UNVERIFIABLE,
        }[state]

    async def probe_status(self, *, timeout: float | None = None) -> ClaudeProviderStatus:
        """Cheap, read-only status probe (no inference; spends no quota).

        Detects the CLI and version and runs ``claude auth status --json`` in the
        same restricted env used for inference, returning one of the safe states
        in ``ClaudeProviderStatus``. The administrator kill switch (DISABLED) is a
        policy concern handled by the caller, not here. Never raises for an
        expected condition; unexpected failures collapse to
        ``AUTH_STATUS_UNVERIFIABLE`` (fail closed for display).
        """

        probe_timeout = min(self._resolve_timeout(timeout), _AUTH_STATUS_TIMEOUT_CEILING_SECONDS)
        try:
            runtime = ensure_claude_runtime()
        except ClaudeProviderError:
            return ClaudeProviderStatus.AUTH_STATUS_UNVERIFIABLE

        try:
            binary = resolve_claude_binary(self._binary_path)
        except ClaudeProviderError:
            return ClaudeProviderStatus.MISSING_CLI

        # Use the "turn-" prefix so cleanup_owned_turn_directory (which only
        # accepts that prefix) can remove it - otherwise every status refresh
        # would leak a directory.
        turn_dir = Path(tempfile.mkdtemp(prefix="turn-", dir=runtime.turns_dir))
        os.chmod(turn_dir, 0o700)
        try:
            try:
                environment = build_claude_environment(binary=binary, turn_dir=turn_dir)
            except ClaudeProviderError:
                return ClaudeProviderStatus.AUTH_STATUS_UNVERIFIABLE
            if self._enforce_version:
                try:
                    await verify_claude_version(
                        binary,
                        environment,
                        turn_dir,
                        on_unreaped_group=lambda pgid: record_uncertain_groups(runtime.root, [pgid]),
                    )
                except ClaudeProviderError:
                    return ClaudeProviderStatus.WRONG_VERSION
            try:
                returncode, stdout = await self._spawn_and_collect(
                    argv=[str(binary), *AUTH_STATUS_ARGV_TAIL],
                    environment=environment,
                    cwd=turn_dir,
                    runtime=runtime,
                    stdin_bytes=None,
                    stdout_limit=MAX_AUTH_STATUS_BYTES,
                    timeout=probe_timeout,
                    task_name="claude-status-probe-spawn",
                )
            except ClaudeProviderError:
                return ClaudeProviderStatus.AUTH_STATUS_UNVERIFIABLE
            state = classify_subscription_auth(stdout, returncode)
            return self._auth_state_to_status(state)
        finally:
            try:
                cleanup_owned_turn_directory(turn_dir, runtime.turns_dir)
            except BaseException:
                pass

    async def run(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        timeout: float | None = None,
    ) -> ProviderTurnResult:
        turn_timeout = self._resolve_timeout(timeout)
        runtime = ensure_claude_runtime()
        # Strict quarantine: refuse a new turn until any prior uncertain process
        # group is proven gone.
        reconcile_claude_quarantine(runtime.root)

        tool_names = [tool["function"]["name"] for tool in tools]
        prompt = build_claude_prompt(messages, tools)

        turn_dir = Path(tempfile.mkdtemp(prefix="turn-", dir=runtime.turns_dir))
        os.chmod(turn_dir, 0o700)
        body_error: BaseException | None = None
        result: ProviderTurnResult | None = None
        try:
            binary = resolve_claude_binary(self._binary_path)
            environment = build_claude_environment(binary=binary, turn_dir=turn_dir)
            if self._enforce_version:
                await verify_claude_version(
                    binary,
                    environment,
                    turn_dir,
                    on_unreaped_group=lambda pgid: record_uncertain_groups(runtime.root, [pgid]),
                )

            # Preflight (locked subscription-only requirement): verify the
            # effective billing route is a subscription login, in the SAME env
            # used for inference, before EVERY turn. Fail closed on anything else.
            auth_timeout = min(turn_timeout, _AUTH_STATUS_TIMEOUT_CEILING_SECONDS)
            auth_returncode, auth_stdout = await self._spawn_and_collect(
                argv=[str(binary), *AUTH_STATUS_ARGV_TAIL],
                environment=environment,
                cwd=turn_dir,
                runtime=runtime,
                stdin_bytes=None,
                stdout_limit=MAX_AUTH_STATUS_BYTES,
                timeout=auth_timeout,
                task_name="claude-auth-status-spawn",
            )
            auth_state = classify_subscription_auth(auth_stdout, auth_returncode)
            if auth_state is not ClaudeSubscriptionAuthState.SUBSCRIPTION_READY:
                raise self._auth_state_error(auth_state)

            # Inference: one fresh, tool-disabled model turn.
            inf_returncode, inf_stdout = await self._spawn_and_collect(
                argv=self._argv(binary),
                environment=environment,
                cwd=turn_dir,
                runtime=runtime,
                stdin_bytes=prompt.encode("utf-8"),
                stdout_limit=MAX_CLAUDE_STDOUT_BYTES,
                timeout=turn_timeout,
                task_name="claude-model-spawn",
            )
            result = parse_claude_result(
                inf_stdout, inf_returncode, allowed_tool_names=set(tool_names)
            )
        except BaseException as exc:  # re-raised after the single turn-dir cleanup
            body_error = exc

        # Clean the shared turn directory exactly once. A leaked ephemeral dir
        # (no credentials, no process) fails THIS turn closed but is NOT a
        # process-group quarantine (there is nothing alive to reconcile).
        cleanup_error: BaseException | None = None
        try:
            cleanup_owned_turn_directory(turn_dir, runtime.turns_dir)
        except BaseException as exc:
            cleanup_error = exc

        if body_error is not None:
            raise body_error
        if cleanup_error is not None:
            raise ClaudeProviderError(
                "cleanup_failed", "Claude could not remove the turn working directory."
            ) from cleanup_error
        if result is None:
            raise ClaudeProviderError(
                "provider_failed", "Claude could not complete the model turn."
            )
        return result

    async def _spawn_and_collect(
        self,
        *,
        argv: list[str],
        environment: dict[str, str],
        cwd: Path,
        runtime: ClaudeRuntime,
        stdin_bytes: bytes | None,
        stdout_limit: int,
        timeout: float,
        task_name: str,
    ) -> tuple[int, bytes]:
        """Run one fully-owned subprocess; quarantine its group if it can't be reaped."""

        process: asyncio.subprocess.Process | None = None
        process_group_id: int | None = None
        wait_task: asyncio.Task[int] | None = None
        reader_tasks: tuple[asyncio.Task[bytes], ...] = ()
        result_data: tuple[int, bytes] | None = None
        pending_error: BaseException | None = None
        try:
            process, spawn_cancellation = await spawn_owned_subprocess(
                *argv,
                task_name=task_name,
                cwd=cwd,
                env=environment,
                stdin=asyncio.subprocess.PIPE if stdin_bytes is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=os.name == "posix",
            )
            if os.name == "posix":
                process_group_id = process.pid
            if spawn_cancellation is not None:
                raise spawn_cancellation
            stdin_writer = process.stdin
            stdout_reader = process.stdout
            stderr_reader = process.stderr
            if stdout_reader is None or stderr_reader is None:
                raise ClaudeProviderError(
                    "provider_failed", "Claude could not complete the model turn."
                )
            if stdin_bytes is not None and stdin_writer is None:
                raise ClaudeProviderError(
                    "provider_failed", "Claude could not complete the model turn."
                )
            wait_task = asyncio.create_task(wait_process_exit(process))
            stdout_task = asyncio.create_task(read_limited(stdout_reader, stdout_limit))
            stderr_task = asyncio.create_task(
                read_limited(stderr_reader, MAX_CLAUDE_STDERR_BYTES)
            )
            reader_tasks = (stdout_task, stderr_task)
            async with asyncio.timeout(timeout):
                if stdin_bytes is not None and stdin_writer is not None:
                    stdin_writer.write(stdin_bytes)
                    await stdin_writer.drain()
                    stdin_writer.close()
                    await stdin_writer.wait_closed()
                returncode, stdout, _stderr = await asyncio.gather(
                    wait_task, stdout_task, stderr_task
                )
            result_data = (returncode, stdout)
        except TimeoutError as exc:
            pending_error = ClaudeProviderError("timeout", "Claude exceeded the configured turn timeout.")
            pending_error.__cause__ = exc
        except asyncio.CancelledError as exc:
            pending_error = exc
        except ClaudeProviderError as exc:
            pending_error = exc
        except Exception as exc:
            pending_error = ClaudeProviderError("provider_failed", "Claude could not complete the model turn.")
            pending_error.__cause__ = exc

        async def finalize() -> None:
            reap_failed = False
            if process is not None:
                try:
                    await terminate_process_group(
                        process,
                        process_group_id,
                        lambda _event, **_fields: None,
                        grace_seconds=PROCESS_TERMINATE_GRACE_SECONDS,
                    )
                except BaseException:
                    reap_failed = True
            for task in reader_tasks:
                if not task.done():
                    task.cancel()
            if reader_tasks:
                await asyncio.gather(*reader_tasks, return_exceptions=True)
            if wait_task is not None:
                if not wait_task.done():
                    wait_task.cancel()
                await asyncio.gather(wait_task, return_exceptions=True)
            if reap_failed:
                # Cannot confirm the child group was reaped: quarantine it so NO
                # subsequent turn runs until the group is proven gone.
                if process_group_id is not None:
                    record_uncertain_groups(runtime.root, [process_group_id])
                raise claude_provider_unavailable()

        initial_cancellation = (
            pending_error if isinstance(pending_error, asyncio.CancelledError) else None
        )
        cleanup_task = asyncio.create_task(finalize(), name="claude-proc-finalizer")
        try:
            _, cleanup_cancellation = await await_task_uninterruptibly(
                cleanup_task, initial_cancellation
            )
        except Exception as exc:
            pending_error = (
                exc
                if isinstance(exc, ClaudeProviderError) and exc.code == "provider_unavailable"
                else ClaudeProviderError("provider_failed", "Claude could not clean up the model turn.")
            )
            cleanup_cancellation = initial_cancellation

        if pending_error is not None:
            raise pending_error
        if cleanup_cancellation is not None:
            raise cleanup_cancellation
        if result_data is None:
            raise ClaudeProviderError(
                "provider_failed", "Claude could not complete the model turn."
            )
        return result_data
