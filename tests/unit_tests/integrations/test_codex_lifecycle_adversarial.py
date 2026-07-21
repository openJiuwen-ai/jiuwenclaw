from __future__ import annotations

import asyncio
import json
import os
import threading
import textwrap
from pathlib import Path

import pytest

from jiuwenswarm.integrations.ai4research_subscription.app_server import (
    CodexAppServerClient,
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
    release_profile_lock,
)
from jiuwenswarm.integrations.ai4research_subscription.process_lifecycle import (
    ProcessTreeCleanupError,
    process_group_is_empty,
)
from jiuwenswarm.integrations.ai4research_subscription.profiles import (
    build_codex_environment,
    ensure_codex_profile,
)
from jiuwenswarm.integrations.ai4research_subscription.quarantine import (
    profile_is_quarantined,
    quarantine_ownership,
    reconcile_profile_quarantine,
    reset_quarantines_for_tests,
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


def _write_auth(profile) -> None:
    auth_path = profile.root / "auth.json"
    auth_path.write_text("offline-test-credential-metadata-only", encoding="utf-8")
    auth_path.chmod(0o600)


async def _wait_for_file(path: Path) -> None:
    for _ in range(400):
        if path.exists():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"timed out waiting for {path.name}")


async def _wait_for_pids_to_exit(pids: list[int]) -> None:
    for _ in range(400):
        if all(not Path(f"/proc/{pid}").exists() for pid in pids):
            return
        await asyncio.sleep(0.005)


def _model_script(*, content: str = "ok") -> str:
    return f'''#!/usr/bin/env python3
import json, sys
if "--version" in sys.argv:
 print("codex-cli 0.144.5"); raise SystemExit(0)
sys.stdin.read()
payload={{"content":{content!r},"reasoning_content":"","tool_calls":[],"finish_reason":"stop"}}
for event in [
 {{"type":"thread.started","thread_id":"t"}},
 {{"type":"turn.started"}},
 {{"type":"item.completed","item":{{"type":"agent_message","text":json.dumps(payload)}}}},
 {{"type":"turn.completed","usage":{{"input_tokens":1,"cached_input_tokens":0,"output_tokens":1}}}},
]: print(json.dumps(event), flush=True)
'''


@pytest.mark.asyncio
async def test_same_runner_reverifies_atomic_binary_replacement_before_exec(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    _write_auth(profile)
    target = _write_executable(tmp_path / "codex-v1", _model_script(content="first"))
    launcher = tmp_path / "codex"
    launcher.symlink_to(target)
    runner = CodexProcessRunner(binary_path=launcher)

    first = await runner.run(
        messages=[{"role": "user", "content": "hello"}], tools=[], timeout=3,
    )
    assert first.content == "first"

    exec_canary = tmp_path / "wrong-version-reached-exec"
    replacement = _write_executable(
        tmp_path / "codex-v2",
        f'''#!/usr/bin/env python3
import pathlib, sys
if "--version" in sys.argv:
 print("codex-cli 0.144.4"); raise SystemExit(0)
pathlib.Path({str(exec_canary)!r}).write_text("unsafe")
raise SystemExit(9)
''',
    )
    os.replace(replacement, target)

    with pytest.raises(CodexProviderError, match="unsupported_cli"):
        await runner.run(
            messages=[{"role": "user", "content": "again"}], tools=[], timeout=3,
        )
    assert not exec_canary.exists()


@pytest.mark.asyncio
async def test_version_probe_stderr_overflow_is_bounded_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    binary = _write_executable(
        tmp_path / "codex-stderr-overflow",
        '''#!/usr/bin/env python3
import sys
sys.stderr.write("private-stderr-canary" * 512)
print("codex-cli 0.144.5")
''',
    )
    environment = build_codex_environment(
        profile, binary=binary, temporary_dir=profile.runtime_home,
    )
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.codex_binary.MAX_VERSION_OUTPUT_BYTES",
        128,
    )

    with pytest.raises(CodexProviderError) as captured:
        await verify_codex_version(binary, environment, profile)
    assert captured.value.code == "unsupported_cli"
    assert "private-stderr-canary" not in str(captured.value)


@pytest.mark.asyncio
async def test_version_spawn_failure_is_sanitized_without_quarantine(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    binary = _write_executable(tmp_path / "codex-spawn-fail", _model_script())
    environment = build_codex_environment(
        profile, binary=binary, temporary_dir=profile.runtime_home,
    )

    async def fail_spawn(*_args, **_kwargs):
        raise OSError("private spawn failure")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_spawn)
    with pytest.raises(CodexProviderError) as captured:
        await verify_codex_version(binary, environment, profile)
    assert captured.value.code == "unsupported_cli"
    assert "private spawn failure" not in str(captured.value)
    assert not profile_is_quarantined(profile)


@pytest.mark.asyncio
async def test_version_probe_keeps_event_loop_responsive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    binary = _write_executable(
        tmp_path / "codex-slow-version",
        '''#!/usr/bin/env python3
import time
time.sleep(0.15)
print("codex-cli 0.144.5")
''',
    )
    environment = build_codex_environment(
        profile, binary=binary, temporary_dir=profile.runtime_home,
    )
    beats: list[float] = []

    async def ticker() -> None:
        loop = asyncio.get_running_loop()
        while True:
            beats.append(loop.time())
            await asyncio.sleep(0.005)

    task = asyncio.create_task(ticker())
    try:
        await verify_codex_version(binary, environment, profile)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(beats) >= 10
    assert max(b - a for a, b in zip(beats, beats[1:])) < 0.1


def test_fresh_asyncio_run_cycles_do_not_hang_or_leak_turns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    _write_auth(profile)
    binary = _write_executable(tmp_path / "codex-fast", _model_script())

    failures: list[BaseException] = []

    def run_fresh_loops() -> None:
        try:
            for _ in range(8):
                result = asyncio.run(
                    CodexProcessRunner(binary_path=binary, enforce_version=False).run(
                        messages=[{"role": "user", "content": "cycle"}],
                        tools=[],
                        timeout=3,
                    )
                )
                assert result.content == "ok"
        except BaseException as exc:
            failures.append(exc)

    # Keep pytest-asyncio's main-thread policy out of this regression check: each
    # provider turn still receives a genuinely fresh asyncio.run() loop.
    worker = threading.Thread(target=run_fresh_loops, name="codex-fresh-loop-test")
    worker.start()
    worker.join(timeout=15)
    assert not worker.is_alive()
    if failures:
        raise failures[0]
    assert list(profile.turns_dir.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="fd-relative cleanup is POSIX-specific")
def test_turn_cleanup_root_swap_never_follows_replacement_symlink(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    turns_dir = tmp_path / "turns"
    turn_dir = turns_dir / "turn-owned"
    moved = tmp_path / "moved-owned"
    outside = tmp_path / "outside"
    turns_dir.mkdir()
    turn_dir.mkdir()
    outside.mkdir()
    canary = outside / "preserve"
    canary.write_text("safe", encoding="utf-8")
    original_rmdir = os.rmdir

    def swap_before_root_removal(path, *, dir_fd=None):
        if path == turn_dir.name and dir_fd is not None:
            os.rename(turn_dir, moved)
            turn_dir.symlink_to(outside, target_is_directory=True)
        return original_rmdir(path, dir_fd=dir_fd)

    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.turn_directory.os.rmdir",
        swap_before_root_removal,
    )
    with pytest.raises(TurnDirectoryCleanupError):
        cleanup_owned_turn_directory(turn_dir, turns_dir)
    assert canary.read_text(encoding="utf-8") == "safe"


def test_windows_provider_admission_is_explicitly_unsupported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.profiles.os.name", "nt"
    )
    with pytest.raises(CodexProviderError) as captured:
        ensure_codex_profile()
    assert captured.value.code == "unsupported_platform"


@pytest.mark.asyncio
async def test_quarantine_marker_is_private_atomic_and_crash_reconcilable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    quarantine_ownership(profile, process=None, pgid=None)
    marker = profile.quarantine_path
    original = marker.read_bytes()
    assert marker.stat().st_mode & 0o777 == 0o600
    decoded = json.loads(original)
    assert set(decoded) == {"version", "boot_id", "pgid", "members", "turn_name"}

    with monkeypatch.context() as scoped:
        scoped.setattr(
            "jiuwenswarm.integrations.ai4research_subscription.quarantine.os.replace",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("replace failed")),
        )
        with pytest.raises(OSError):
            quarantine_ownership(profile, process=None, pgid=None)
    assert marker.read_bytes() == original

    reset_quarantines_for_tests()
    await reconcile_profile_quarantine(profile)
    assert not marker.exists()
    assert not profile_is_quarantined(profile)


@pytest.mark.asyncio
async def test_quarantine_marker_symlink_and_stale_boot_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    outside = tmp_path / "outside-marker"
    outside.write_text("preserve", encoding="utf-8")
    profile.quarantine_path.symlink_to(outside)
    with pytest.raises(CodexProviderError, match="provider_quarantined"):
        await reconcile_profile_quarantine(profile)
    assert outside.read_text(encoding="utf-8") == "preserve"
    profile.quarantine_path.unlink()

    profile.quarantine_path.write_text(
        json.dumps(
            {
                "version": 1,
                "boot_id": "definitely-not-the-current-boot",
                "pgid": 2147483000,
                "members": [],
                "turn_name": None,
            }
        ),
        encoding="utf-8",
    )
    profile.quarantine_path.chmod(0o600)
    with pytest.raises(CodexProviderError, match="provider_quarantined"):
        await reconcile_profile_quarantine(profile)
    assert profile.quarantine_path.exists()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process-group cleanup is POSIX-specific")
async def test_model_spawn_double_cancel_owns_tree_and_omits_comm_canary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    _write_auth(profile)
    pid_path = tmp_path / "model-spawn-pids.json"
    binary = _write_executable(
        tmp_path / "codex-model-spawn",
        f'''#!/usr/bin/env python3
import json, os, pathlib, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path("/proc/self/comm").write_text("secret-comm-canary")
child_code = "import pathlib,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); pathlib.Path('/proc/self/comm').write_text('secret-child'); time.sleep(60)"
child = subprocess.Popen([sys.executable, "-c", child_code])
pathlib.Path({str(pid_path)!r}).write_text(json.dumps([os.getpid(), child.pid]))
time.sleep(60)
''',
    )
    original_spawn = asyncio.create_subprocess_exec

    async def delayed_spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        await asyncio.sleep(0.12)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.codex_process.PROCESS_TERMINATE_GRACE_SECONDS",
        0.25,
    )
    runner = CodexProcessRunner(binary_path=binary, enforce_version=False)
    beats: list[float] = []

    async def ticker() -> None:
        loop = asyncio.get_running_loop()
        while True:
            beats.append(loop.time())
            await asyncio.sleep(0.005)

    ticker_task = asyncio.create_task(ticker())
    task = asyncio.create_task(
        runner.run(messages=[{"role": "user", "content": "cancel"}], tools=[], timeout=5)
    )
    await _wait_for_file(pid_path)
    task.cancel()
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    ticker_task.cancel()
    await asyncio.gather(ticker_task, return_exceptions=True)

    pids = json.loads(pid_path.read_text(encoding="utf-8"))
    await _wait_for_pids_to_exit(pids)
    assert all(not Path(f"/proc/{pid}").exists() for pid in pids)
    assert list(profile.turns_dir.iterdir()) == []
    recovered = acquire_profile_lock(profile)
    release_profile_lock(recovered)
    rendered = json.dumps(runner.lifecycle_evidence)
    assert "secret-comm-canary" not in rendered
    assert "secret-child" not in rendered
    assert all("comm" not in event for event in runner.lifecycle_evidence)
    reaped = [event for event in runner.lifecycle_evidence if event["event"] == "group_reaped"]
    assert reaped and reaped[-1]["group_empty"] is True
    assert reaped[-1]["cleanup_elapsed_seconds"] <= 0.5
    assert reaped[-1]["cleanup_deadline_seconds"] == 0.25
    assert reaped[-1]["process_scan_count"] <= 20
    assert len(beats) >= 10
    assert max(b - a for a, b in zip(beats, beats[1:])) < 0.12


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process-group cleanup is POSIX-specific")
async def test_version_spawn_double_cancel_owns_real_leader_and_descendant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    pid_path = tmp_path / "version-spawn-pids.json"
    binary = _write_executable(
        tmp_path / "codex-version-spawn",
        f'''#!/usr/bin/env python3
import json, os, pathlib, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])
pathlib.Path({str(pid_path)!r}).write_text(json.dumps([os.getpid(), child.pid]))
time.sleep(60)
''',
    )
    environment = build_codex_environment(
        profile, binary=binary, temporary_dir=profile.runtime_home,
    )
    original_spawn = asyncio.create_subprocess_exec

    async def delayed_spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        await asyncio.sleep(0.12)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.codex_binary.PROCESS_TERMINATE_GRACE_SECONDS",
        0.25,
    )
    task = asyncio.create_task(verify_codex_version(binary, environment, profile))
    await _wait_for_file(pid_path)
    task.cancel()
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pids = json.loads(pid_path.read_text(encoding="utf-8"))
    await _wait_for_pids_to_exit(pids)
    assert all(not Path(f"/proc/{pid}").exists() for pid in pids)
    assert not profile_is_quarantined(profile)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process-group cleanup is POSIX-specific")
async def test_app_server_spawn_double_cancel_owns_real_leader_and_descendant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    pid_path = tmp_path / "app-spawn-pids.json"
    binary = _write_executable(
        tmp_path / "codex-app-spawn",
        f'''#!/usr/bin/env python3
import json, os, pathlib, signal, subprocess, sys, time
if "--version" in sys.argv:
 print("codex-cli 0.144.5"); raise SystemExit(0)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])
pathlib.Path({str(pid_path)!r}).write_text(json.dumps([os.getpid(), child.pid]))
time.sleep(60)
''',
    )
    environment = build_codex_environment(
        profile, binary=binary, temporary_dir=profile.runtime_home,
    )
    await verify_codex_version(binary, environment, profile)
    original_spawn = asyncio.create_subprocess_exec

    async def delayed_spawn(*args, **kwargs):
        process = await original_spawn(*args, **kwargs)
        await asyncio.sleep(0.12)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.app_server.PROCESS_TERMINATE_GRACE_SECONDS",
        0.25,
    )
    client = CodexAppServerClient(profile, binary_path=binary)
    task = asyncio.create_task(client.start())
    await _wait_for_file(pid_path)
    task.cancel()
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pids = json.loads(pid_path.read_text(encoding="utf-8"))
    await _wait_for_pids_to_exit(pids)
    assert all(not Path(f"/proc/{pid}").exists() for pid in pids)
    assert client._process is None
    assert not profile_is_quarantined(profile)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process-group cleanup is POSIX-specific")
async def test_model_cleanup_failure_dominates_cancellation_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    _write_auth(profile)
    pid_path = tmp_path / "model-cleanup-pids.json"
    binary = _write_executable(
        tmp_path / "codex-model-cleanup",
        f'''#!/usr/bin/env python3
import json, os, pathlib, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])
pathlib.Path({str(pid_path)!r}).write_text(json.dumps([os.getpid(), child.pid]))
time.sleep(60)
''',
    )

    async def fail_cleanup(*_args, **_kwargs):
        raise ProcessTreeCleanupError("injected tree uncertainty")

    runner = CodexProcessRunner(binary_path=binary, enforce_version=False)
    with monkeypatch.context() as scoped:
        scoped.setattr(
            "jiuwenswarm.integrations.ai4research_subscription.codex_process.terminate_process_group",
            fail_cleanup,
        )
        task = asyncio.create_task(
            runner.run(messages=[{"role": "user", "content": "cancel"}], tools=[], timeout=5)
        )
        await _wait_for_file(pid_path)
        task.cancel()
        with pytest.raises(CodexProviderError) as captured:
            await task
        assert captured.value.code == "provider_quarantined"
    assert profile_is_quarantined(profile)
    with pytest.raises(CodexProviderError, match="provider_busy"):
        acquire_profile_lock(profile)
    await reconcile_profile_quarantine(profile)
    pids = json.loads(pid_path.read_text(encoding="utf-8"))
    await _wait_for_pids_to_exit(pids)
    assert all(not Path(f"/proc/{pid}").exists() for pid in pids)
    assert not profile_is_quarantined(profile)
    recovered = acquire_profile_lock(profile)
    release_profile_lock(recovered)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process-group cleanup is POSIX-specific")
async def test_version_cleanup_failure_dominates_timeout_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    pid_path = tmp_path / "version-cleanup-pids.json"
    binary = _write_executable(
        tmp_path / "codex-version-cleanup",
        f'''#!/usr/bin/env python3
import json, os, pathlib, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])
pathlib.Path({str(pid_path)!r}).write_text(json.dumps([os.getpid(), child.pid]))
time.sleep(60)
''',
    )
    environment = build_codex_environment(
        profile, binary=binary, temporary_dir=profile.runtime_home,
    )
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.codex_binary.VERSION_VERIFY_TIMEOUT_SECONDS",
        0.05,
    )

    async def fail_cleanup(*_args, **_kwargs):
        raise ProcessTreeCleanupError("injected version uncertainty")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            "jiuwenswarm.integrations.ai4research_subscription.codex_binary.terminate_process_group",
            fail_cleanup,
        )
        with pytest.raises(CodexProviderError) as captured:
            await verify_codex_version(binary, environment, profile)
        assert captured.value.code == "provider_quarantined"
    assert profile_is_quarantined(profile)
    await reconcile_profile_quarantine(profile)
    pids = json.loads(pid_path.read_text(encoding="utf-8"))
    await _wait_for_pids_to_exit(pids)
    assert all(not Path(f"/proc/{pid}").exists() for pid in pids)


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="process-group cleanup is POSIX-specific")
async def test_app_server_close_failure_retains_owner_until_reconciled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _patch_workspace(monkeypatch, tmp_path)
    profile = ensure_codex_profile()
    binary = _write_executable(
        tmp_path / "codex-app-server",
        '''#!/usr/bin/env python3
import json, signal, sys
if "--version" in sys.argv:
 print("codex-cli 0.144.5"); raise SystemExit(0)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
for line in sys.stdin:
 frame=json.loads(line); rid=frame.get("id")
 if rid is not None:
  print(json.dumps({"id":rid,"result":{"serverInfo":{"name":"codex","version":"0.144.5"}}}), flush=True)
''',
    )
    client = CodexAppServerClient(profile, binary_path=binary)
    await client.start()
    owned_process = client._process
    assert owned_process is not None

    async def fail_cleanup(*_args, **_kwargs):
        raise ProcessTreeCleanupError("injected app-server uncertainty")

    with monkeypatch.context() as scoped:
        scoped.setattr(
            "jiuwenswarm.integrations.ai4research_subscription.app_server.terminate_process",
            fail_cleanup,
        )
        with pytest.raises(CodexProviderError) as captured:
            await client.close()
        assert captured.value.code == "provider_quarantined"
    assert client._process is owned_process
    assert profile_is_quarantined(profile)
    await client.close()
    assert client._process is None
    assert process_group_is_empty(owned_process.pid)
