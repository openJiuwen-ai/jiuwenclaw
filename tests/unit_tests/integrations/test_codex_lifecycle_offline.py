from __future__ import annotations

import asyncio
import json
import os
import textwrap
from pathlib import Path

import pytest

from jiuwenswarm.integrations.ai4research_subscription.auth_controller import (
    CodexAuthController,
)
from jiuwenswarm.integrations.ai4research_subscription.codex_binary import (
    verify_codex_version,
)
from jiuwenswarm.integrations.ai4research_subscription.codex_process import (
    CodexProcessRunner,
)
from jiuwenswarm.integrations.ai4research_subscription.errors import CodexProviderError
from jiuwenswarm.integrations.ai4research_subscription.locking import (
    acquire_profile_lock,
    acquire_profile_lock_async,
    release_profile_lock,
)
from jiuwenswarm.integrations.ai4research_subscription.profiles import (
    build_codex_environment,
    ensure_codex_profile,
)
from jiuwenswarm.integrations.ai4research_subscription.quarantine import (
    profile_is_quarantined,
    reconcile_profile_quarantine,
)
from jiuwenswarm.integrations.ai4research_subscription.turn_directory import (
    TurnDirectoryCleanupError,
    cleanup_owned_turn_directory,
)


def _patch_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    workspace = tmp_path / "instance"
    workspace.mkdir(mode=0o700)
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.profiles.get_user_workspace_dir",
        lambda: workspace,
    )


def _write_executable(path: Path, source: str) -> Path:
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    path.chmod(0o700)
    return path


def _version_arguments(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, source: str):
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    binary = _write_executable(tmp_path / "codex-private-path-canary", source)
    environment = build_codex_environment(
        profile,
        binary=binary,
        temporary_dir=profile.runtime_home,
    )
    return binary, environment, profile


async def _wait_for_pids_to_exit(pids: list[int]) -> None:
    for _ in range(200):
        if all(not Path(f"/proc/{pid}").exists() for pid in pids):
            return
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_async_version_probe_accepts_exact_version_and_caches_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    binary, environment, cwd = _version_arguments(
        monkeypatch,
        tmp_path,
        r'''#!/usr/bin/env python3
import pathlib
counter = pathlib.Path(__file__).with_name("version-count")
counter.write_text(str(int(counter.read_text()) + 1) if counter.exists() else "1")
print("codex-cli 0.144.5")
''',
    )

    await verify_codex_version(binary, environment, cwd)
    await verify_codex_version(binary, environment, cwd)

    assert (tmp_path / "version-count").read_text(encoding="utf-8") == "1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        ('print("codex-cli 0.144.4")', "unsupported_cli"),
        ('print("codex-cli 0.144.5 extra")', "unsupported_cli"),
        ('print("codex-cli 0.144.5"); raise SystemExit(7)', "unsupported_cli"),
        ('print("private-output-canary" * 512)', "unsupported_cli"),
    ],
)
async def test_async_version_probe_rejects_wrong_nonzero_and_overflow_without_leakage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    body: str,
    expected_code: str,
) -> None:
    binary, environment, cwd = _version_arguments(
        monkeypatch,
        tmp_path,
        f"#!/usr/bin/env python3\n{body}\n",
    )
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.codex_binary."
        "MAX_VERSION_OUTPUT_BYTES",
        128,
    )

    with pytest.raises(CodexProviderError) as captured:
        await verify_codex_version(binary, environment, cwd)

    assert captured.value.code == expected_code
    rendered = str(captured.value)
    assert "private-output-canary" not in rendered
    assert str(binary) not in rendered


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process-group cleanup is POSIX-specific")
@pytest.mark.parametrize("mode", ["timeout", "cancel"])
async def test_async_version_probe_timeout_and_cancel_kill_term_ignoring_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
) -> None:
    binary, environment, cwd = _version_arguments(
        monkeypatch,
        tmp_path,
        r'''#!/usr/bin/env python3
import json, os, pathlib, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
base = pathlib.Path(__file__).parent
ready = base / "version-child-ready"
child_code = "import pathlib,signal,sys,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); pathlib.Path(sys.argv[1]).write_text('ready'); time.sleep(60)"
child = subprocess.Popen([sys.executable, "-c", child_code, str(ready)])
while not ready.exists(): time.sleep(0.005)
(base / "version-pids.json").write_text(json.dumps([os.getpid(), child.pid]))
time.sleep(60)
''',
    )
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.codex_binary."
        "VERSION_VERIFY_TIMEOUT_SECONDS",
        0.1 if mode == "timeout" else 10.0,
    )
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.codex_binary."
        "PROCESS_TERMINATE_GRACE_SECONDS",
        0.1,
    )

    task = asyncio.create_task(verify_codex_version(binary, environment, cwd))
    pid_path = tmp_path / "version-pids.json"
    for _ in range(200):
        if pid_path.exists():
            break
        await asyncio.sleep(0.005)
    assert pid_path.exists()
    if mode == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(CodexProviderError, match="unsupported_cli"):
            await task

    pids = json.loads(pid_path.read_text(encoding="utf-8"))
    await _wait_for_pids_to_exit(pids)
    assert all(not Path(f"/proc/{pid}").exists() for pid in pids)


@pytest.mark.asyncio
async def test_lock_contention_keeps_loop_responsive_then_recovers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    held = acquire_profile_lock(profile)
    heartbeat = 0

    async def beat() -> None:
        nonlocal heartbeat
        while True:
            heartbeat += 1
            await asyncio.sleep(0.005)

    heartbeat_task = asyncio.create_task(beat())
    try:
        with pytest.raises(CodexProviderError, match="provider_busy"):
            await acquire_profile_lock_async(profile)
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        release_profile_lock(held)
    assert heartbeat >= 10

    recovered = await acquire_profile_lock_async(profile)
    release_profile_lock(recovered)


@pytest.mark.asyncio
async def test_lock_wait_cancellation_does_not_leak_handle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    held = acquire_profile_lock(profile)
    waiter = asyncio.create_task(acquire_profile_lock_async(profile))
    await asyncio.sleep(0.03)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release_profile_lock(held)

    recovered = await acquire_profile_lock_async(profile)
    release_profile_lock(recovered)


def test_turn_cleanup_unlinks_symlink_without_touching_target(tmp_path: Path) -> None:
    turns_dir = tmp_path / "turns"
    turn_dir = turns_dir / "turn-owned"
    turns_dir.mkdir()
    turn_dir.mkdir()
    outside = tmp_path / "outside-canary"
    outside.write_text("preserve", encoding="utf-8")
    (turn_dir / "link").symlink_to(outside)
    nested = turn_dir / "nested"
    nested.mkdir()
    (nested / "data").write_text("owned", encoding="utf-8")

    cleanup_owned_turn_directory(turn_dir, turns_dir)

    assert not turn_dir.exists()
    assert outside.read_text(encoding="utf-8") == "preserve"


def test_turn_cleanup_fails_closed_at_entry_bound(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    turns_dir = tmp_path / "turns"
    turn_dir = turns_dir / "turn-owned"
    turns_dir.mkdir()
    turn_dir.mkdir()
    (turn_dir / "one").write_text("1", encoding="utf-8")
    (turn_dir / "two").write_text("2", encoding="utf-8")
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.turn_directory."
        "MAX_TURN_CLEANUP_ENTRIES",
        1,
    )

    with pytest.raises(TurnDirectoryCleanupError, match="entry limit"):
        cleanup_owned_turn_directory(turn_dir, turns_dir)

    assert turn_dir.is_dir()
    assert {path.name for path in turn_dir.iterdir()} == {"one", "two"}


@pytest.mark.asyncio
async def test_turn_cleanup_failure_quarantines_until_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    auth_path = profile.root / "auth.json"
    auth_path.write_text("not-a-real-token", encoding="utf-8")
    auth_path.chmod(0o600)
    binary = _write_executable(
        tmp_path / "codex",
        r'''#!/usr/bin/env python3
import json, sys
if "--version" in sys.argv:
 print("codex-cli 0.144.5"); raise SystemExit(0)
sys.stdin.read()
payload={"content":"valid","reasoning_content":"","tool_calls":[],"finish_reason":"stop"}
for event in [
 {"type":"thread.started","thread_id":"t"},
 {"type":"turn.started"},
 {"type":"item.completed","item":{"type":"agent_message","text":json.dumps(payload)}},
 {"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}},
]: print(json.dumps(event), flush=True)
''',
    )
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.turn_directory."
        "MAX_TURN_CLEANUP_ENTRIES",
        0,
    )

    with pytest.raises(CodexProviderError, match="provider_quarantined"):
        await CodexProcessRunner(binary_path=binary).run(
            messages=[{"role": "user", "content": "hello"}], tools=[], timeout=5,
        )

    assert list(profile.turns_dir.iterdir())
    assert profile_is_quarantined(profile)
    with pytest.raises(CodexProviderError, match="provider_busy"):
        acquire_profile_lock(profile)
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.turn_directory."
        "MAX_TURN_CLEANUP_ENTRIES",
        256,
    )
    await reconcile_profile_quarantine(profile)
    assert not profile_is_quarantined(profile)
    recovered = acquire_profile_lock(profile)
    release_profile_lock(recovered)


@pytest.mark.asyncio
async def test_model_and_auth_paths_never_use_default_executor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    auth_path = profile.root / "auth.json"
    auth_path.write_text("not-a-real-token", encoding="utf-8")
    auth_path.chmod(0o600)
    binary = _write_executable(
        tmp_path / "codex",
        r'''#!/usr/bin/env python3
import json, sys
if "--version" in sys.argv:
 print("codex-cli 0.144.5"); raise SystemExit(0)
if sys.argv[1] == "exec":
 sys.stdin.read()
 payload={"content":"executor-free","reasoning_content":"","tool_calls":[],"finish_reason":"stop"}
 for event in [
  {"type":"thread.started","thread_id":"t"},
  {"type":"turn.started"},
  {"type":"item.completed","item":{"type":"agent_message","text":json.dumps(payload)}},
  {"type":"turn.completed","usage":{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}},
 ]: print(json.dumps(event), flush=True)
 raise SystemExit(0)
for line in sys.stdin:
 frame=json.loads(line); method=frame.get("method"); rid=frame.get("id")
 if method == "initialized": continue
 if method == "initialize": result={"serverInfo":{"name":"codex","version":"0.144.5"}}
 elif method == "account/read": result={"account":{"type":"chatgpt"},"requiresOpenaiAuth":True}
 else: result={}
 if rid is not None: print(json.dumps({"id":rid,"result":result}), flush=True)
''',
    )
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.codex_binary.shutil.which",
        lambda _name: str(binary),
    )

    async def forbidden_to_thread(*_args, **_kwargs):
        raise AssertionError("provider path used asyncio.to_thread")

    monkeypatch.setattr(asyncio, "to_thread", forbidden_to_thread)
    result = await CodexProcessRunner(binary_path=binary).run(
        messages=[{"role": "user", "content": "hello"}], tools=[], timeout=5,
    )
    assert result.content == "executor-free"
    controller = CodexAuthController()
    assert (await controller.status())["connected"] is True
    await controller.shutdown()
