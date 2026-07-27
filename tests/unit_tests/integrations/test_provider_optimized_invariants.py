"""Fail-closed provider invariants that must survive optimized Python."""

from __future__ import annotations

import ast
import asyncio
import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from jiuwenswarm.integrations.ai4research_subscription import (
    app_server,
    claude_binary,
    claude_process,
    codex_binary,
    codex_process,
)
from jiuwenswarm.integrations.ai4research_subscription.app_server import (
    CodexAppServerClient,
)
from jiuwenswarm.integrations.ai4research_subscription.claude_process import (
    ClaudeProcessRunner,
    ClaudeRuntime,
)
from jiuwenswarm.integrations.ai4research_subscription.codex_process import (
    CodexProcessRunner,
)
from jiuwenswarm.integrations.ai4research_subscription.errors import (
    ClaudeProviderError,
    CodexProviderError,
)


class _MissingPipeProcess:
    pid = 424242
    returncode = None
    stdin = None
    stdout = None
    stderr = None


def _write_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def test_provider_ownership_modules_have_no_optimization_sensitive_asserts() -> None:
    modules = (
        app_server,
        claude_binary,
        claude_process,
        codex_binary,
        codex_process,
    )
    for module in modules:
        tree = ast.parse(inspect.getsource(module), filename=module.__file__)
        assert not any(isinstance(node, ast.Assert) for node in ast.walk(tree))


@pytest.mark.asyncio
async def test_app_server_reader_rejects_missing_pipe_with_typed_error(tmp_path: Path) -> None:
    profile = SimpleNamespace(runtime_home=tmp_path)
    binary = _write_executable(tmp_path / "codex")
    client = CodexAppServerClient(profile, binary_path=binary)

    with pytest.raises(CodexProviderError) as caught:
        await client._read_frames(None)

    assert caught.value.code == "auth_protocol_error"


@pytest.mark.asyncio
async def test_app_server_start_rejects_missing_pipes_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile = SimpleNamespace(runtime_home=tmp_path)
    binary = _write_executable(tmp_path / "codex")
    client = CodexAppServerClient(profile, binary_path=binary)
    cleanup_calls = 0

    async def no_reconcile(_profile) -> None:
        return None

    async def no_verify(*_args, **_kwargs) -> None:
        return None

    async def missing_pipe_spawn(*_args, **_kwargs):
        return _MissingPipeProcess(), None

    async def record_close() -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1

    monkeypatch.setattr(app_server, "reconcile_profile_quarantine", no_reconcile)
    monkeypatch.setattr(app_server, "build_codex_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(app_server, "verify_codex_version", no_verify)
    monkeypatch.setattr(app_server, "spawn_owned_subprocess", missing_pipe_spawn)
    monkeypatch.setattr(client, "close", record_close)

    with pytest.raises(CodexProviderError) as caught:
        await client.start()

    assert caught.value.code == "auth_protocol_error"
    assert cleanup_calls == 1


@pytest.mark.asyncio
async def test_app_server_start_chains_cleanup_failure_from_start_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile = SimpleNamespace(runtime_home=tmp_path)
    binary = _write_executable(tmp_path / "codex")
    client = CodexAppServerClient(profile, binary_path=binary)

    async def no_reconcile(_profile) -> None:
        return None

    async def no_verify(*_args, **_kwargs) -> None:
        return None

    async def missing_pipe_spawn(*_args, **_kwargs):
        return _MissingPipeProcess(), None

    cleanup_failure = RuntimeError("cleanup failed")

    async def fail_close() -> None:
        raise cleanup_failure

    monkeypatch.setattr(app_server, "reconcile_profile_quarantine", no_reconcile)
    monkeypatch.setattr(app_server, "build_codex_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(app_server, "verify_codex_version", no_verify)
    monkeypatch.setattr(app_server, "spawn_owned_subprocess", missing_pipe_spawn)
    monkeypatch.setattr(client, "close", fail_close)

    with pytest.raises(RuntimeError) as caught:
        await client.start()

    assert caught.value is cleanup_failure
    assert isinstance(caught.value.__cause__, CodexProviderError)
    assert caught.value.__cause__.code == "auth_protocol_error"


@pytest.mark.asyncio
async def test_app_server_start_cancellation_cleans_up_without_self_cause(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile = SimpleNamespace(runtime_home=tmp_path)
    binary = _write_executable(tmp_path / "codex")
    client = CodexAppServerClient(profile, binary_path=binary)
    cancellation = asyncio.CancelledError("spawn canceled")
    cleanup_calls = 0

    async def no_reconcile(_profile) -> None:
        return None

    async def no_verify(*_args, **_kwargs) -> None:
        return None

    async def canceled_spawn(*_args, **_kwargs):
        return _MissingPipeProcess(), cancellation

    async def record_close() -> None:
        nonlocal cleanup_calls
        cleanup_calls += 1

    monkeypatch.setattr(app_server, "reconcile_profile_quarantine", no_reconcile)
    monkeypatch.setattr(app_server, "build_codex_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(app_server, "verify_codex_version", no_verify)
    monkeypatch.setattr(app_server, "spawn_owned_subprocess", canceled_spawn)
    monkeypatch.setattr(client, "close", record_close)

    with pytest.raises(asyncio.CancelledError) as caught:
        await client.start()

    assert caught.value is cancellation
    assert caught.value.__cause__ is None
    assert cleanup_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "verify_name", "expected_type"),
    (
        (codex_binary, "codex", CodexProviderError),
        (claude_binary, "claude", ClaudeProviderError),
    ),
)
async def test_version_probe_missing_pipes_fails_typed_after_reap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    module,
    verify_name: str,
    expected_type: type[Exception],
) -> None:
    binary = _write_executable(tmp_path / verify_name)
    reaped = False

    async def missing_pipe_spawn(*_args, **_kwargs):
        return _MissingPipeProcess(), None

    async def record_reap(*_args, **_kwargs) -> None:
        nonlocal reaped
        reaped = True

    monkeypatch.setattr(module, "spawn_owned_subprocess", missing_pipe_spawn)
    monkeypatch.setattr(module, "terminate_process_group", record_reap)
    module._VERIFIED_EXECUTABLES.clear()

    with pytest.raises(expected_type) as caught:
        if module is codex_binary:
            profile = SimpleNamespace(runtime_home=tmp_path)
            await module.verify_codex_version(binary, {}, profile)
        else:
            await module.verify_claude_version(binary, {}, tmp_path)

    assert caught.value.code == "unsupported_cli"
    assert reaped is True


@pytest.mark.skipif(os.name != "posix", reason="process-group cleanup is POSIX-specific")
@pytest.mark.asyncio
async def test_claude_version_callback_failure_is_logged_and_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary = _write_executable(tmp_path / "claude")

    async def missing_pipe_spawn(*_args, **_kwargs):
        return _MissingPipeProcess(), None

    async def fail_reap(*_args, **_kwargs) -> None:
        raise RuntimeError("private cleanup detail")

    def fail_callback(_pgid: int) -> None:
        raise RuntimeError("private callback detail")

    warnings: list[tuple[str, str]] = []

    def record_warning(message: str, detail: str) -> None:
        warnings.append((message, detail))

    monkeypatch.setattr(claude_binary, "spawn_owned_subprocess", missing_pipe_spawn)
    monkeypatch.setattr(claude_binary, "terminate_process_group", fail_reap)
    monkeypatch.setattr(claude_binary.logger, "warning", record_warning)
    claude_binary._VERIFIED_EXECUTABLES.clear()

    with pytest.raises(ClaudeProviderError) as caught:
        await claude_binary.verify_claude_version(
            binary,
            {},
            tmp_path,
            on_unreaped_group=fail_callback,
        )

    assert caught.value.code == "provider_unavailable"
    assert warnings == [
        ("Claude version quarantine callback failed: %s", "RuntimeError")
    ]


@pytest.mark.parametrize("module", (codex_binary, claude_binary))
def test_executable_identity_detects_symlink_target_replacement(
    module,
    tmp_path: Path,
) -> None:
    target = _write_executable(tmp_path / "provider-v1")
    launcher = tmp_path / "provider"
    launcher.symlink_to(target)
    first = module._executable_identity(launcher)

    replacement = _write_executable(tmp_path / "provider-v2")
    os.replace(replacement, target)
    second = module._executable_identity(launcher)

    assert first.path == second.path == str(launcher)
    assert first.launcher == second.launcher
    assert first.target != second.target
    assert first != second


@pytest.mark.asyncio
async def test_claude_spawn_missing_pipes_fails_typed_after_reap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = ClaudeProcessRunner(enforce_version=False)
    runtime = ClaudeRuntime(root=tmp_path, turns_dir=tmp_path)
    reaped = False

    async def missing_pipe_spawn(*_args, **_kwargs):
        return _MissingPipeProcess(), None

    async def record_reap(*_args, **_kwargs) -> None:
        nonlocal reaped
        reaped = True

    monkeypatch.setattr(claude_process, "spawn_owned_subprocess", missing_pipe_spawn)
    monkeypatch.setattr(claude_process, "terminate_process_group", record_reap)

    with pytest.raises(ClaudeProviderError) as caught:
        await runner._spawn_and_collect(
            argv=["claude"],
            environment={},
            cwd=tmp_path,
            runtime=runtime,
            stdin_bytes=b"prompt",
            stdout_limit=1024,
            timeout=1.0,
            task_name="missing-pipe-test",
        )

    assert caught.value.code == "provider_failed"
    assert reaped is True


@pytest.mark.asyncio
async def test_codex_turn_missing_pipes_releases_lock_and_removes_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    turns_dir = tmp_path / "turns"
    turns_dir.mkdir()
    profile = SimpleNamespace(
        root=tmp_path,
        runtime_home=tmp_path,
        turns_dir=turns_dir,
    )
    lock_handle = object()
    lock_released = False
    reaped = False

    async def no_reconcile(_profile) -> None:
        return None

    async def acquire_lock(_profile):
        return lock_handle

    async def verified(_profile, _turn_dir) -> Path:
        return tmp_path / "codex"

    async def missing_pipe_spawn(*_args, **_kwargs):
        return _MissingPipeProcess(), None

    async def record_reap(*_args, **_kwargs) -> None:
        nonlocal reaped
        reaped = True

    def release_lock(actual_handle) -> None:
        nonlocal lock_released
        assert actual_handle is lock_handle
        lock_released = True

    runner = CodexProcessRunner(enforce_version=False)
    monkeypatch.setattr(codex_process, "ensure_codex_profile", lambda: profile)
    monkeypatch.setattr(codex_process, "reconcile_profile_quarantine", no_reconcile)
    monkeypatch.setattr(codex_process, "acquire_profile_lock_async", acquire_lock)
    monkeypatch.setattr(codex_process, "verify_codex_auth_file", lambda _profile: None)
    monkeypatch.setattr(codex_process, "build_codex_environment", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(codex_process, "spawn_owned_subprocess", missing_pipe_spawn)
    monkeypatch.setattr(codex_process, "terminate_process_group", record_reap)
    monkeypatch.setattr(codex_process, "profile_is_quarantined", lambda _profile: False)
    monkeypatch.setattr(codex_process, "release_profile_lock", release_lock)
    monkeypatch.setattr(runner, "_verified", verified)

    with pytest.raises(CodexProviderError) as caught:
        await runner.run(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert caught.value.code == "provider_failed"
    assert reaped is True
    assert lock_released is True
    assert list(turns_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_claude_impossible_parsed_result_fails_after_turn_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "instance"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(claude_process, "get_user_workspace_dir", lambda: workspace)
    monkeypatch.setattr(
        claude_process,
        "resolve_claude_binary",
        lambda _candidate: tmp_path / "claude",
    )
    monkeypatch.setattr(claude_process, "parse_claude_result", lambda *_args, **_kwargs: None)
    responses = iter(
        (
            (
                0,
                b'{"loggedIn":true,"authMethod":"claude.ai",'
                b'"apiProvider":"firstParty","subscriptionType":"max"}',
            ),
            (0, b"{}"),
        )
    )

    async def collect(**_kwargs):
        return next(responses)

    runner = ClaudeProcessRunner(enforce_version=False)
    monkeypatch.setattr(runner, "_spawn_and_collect", collect)

    with pytest.raises(ClaudeProviderError) as caught:
        await runner.run(messages=[{"role": "user", "content": "hi"}], tools=[])

    assert caught.value.code == "provider_failed"
    turns_dir = workspace / "private" / "subscription-providers" / "claude" / "turns"
    assert list(turns_dir.iterdir()) == []
