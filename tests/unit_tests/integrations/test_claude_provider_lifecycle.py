"""Process-lifecycle tests for the Claude provider runner.

These prove the process-group ownership claims:

* a Claude turn owns its whole process group: descendants that outlive (or
  ignore SIGTERM from) the parent are still reaped, and the group is empty once
  ``run()`` returns;
* cancellation (single and double) tears the tree down and propagates cleanly.

Cleanup-failure -> strict cross-turn quarantine (record the group, block every
later turn until it is proven gone) is covered leak-free in
test_claude_provider_quarantine.py.

Each fake CLI is a REAL child process (a genuine subprocess used for
deterministic injection), not a mock of the runner. The fake also answers the
``auth status`` preflight with a valid subscription login. POSIX-only:
process-group semantics are POSIX-specific.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from jiuwenswarm.integrations.ai4research_subscription import claude_binary, claude_process
from jiuwenswarm.integrations.ai4research_subscription.claude_process import ClaudeProcessRunner
from jiuwenswarm.integrations.ai4research_subscription.errors import ClaudeProviderError

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="process-group cleanup is POSIX-specific"
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _patch_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    workspace = tmp_path / "instance"
    workspace.mkdir(mode=0o700)
    monkeypatch.setattr(claude_process, "get_user_workspace_dir", lambda: workspace)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return workspace


def _write_executable(path: Path, source: str) -> Path:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o700)
    return path


_SUCCESS_DOC = json.dumps(
    {
        "is_error": False,
        "num_turns": 1,
        "result": json.dumps(
            {
                "content": "lifecycle-ok",
                "reasoning_content": "",
                "tool_calls": [],
                "finish_reason": "stop",
            }
        ),
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
)


def _fake_cli_forking(pid_path: Path, *, parent_lingers: bool) -> str:
    """A real Claude-shaped CLI that forks a SIGTERM-ignoring descendant.

    It records ``[parent_pid, child_pid]`` to ``pid_path`` so the test can assert
    the whole group was reaped. The child ignores SIGTERM to force the group kill
    to escalate to SIGKILL. If ``parent_lingers`` the parent sleeps after emitting
    (used for the cancel tests); otherwise it emits the success document and exits
    0 (used to prove the group is empty after a normal turn).
    """

    tail = "time.sleep(60)" if parent_lingers else f"sys.stdout.write({_SUCCESS_DOC!r}); sys.stdout.flush()"
    return (
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, signal, subprocess, sys, time\n"
        'if "--version" in sys.argv:\n'
        ' print("2.1.218 (Claude Code)"); raise SystemExit(0)\n'
        'if "auth" in sys.argv and "status" in sys.argv:\n'
        ' print(json.dumps({"loggedIn": True, "authMethod": "claude.ai",'
        ' "apiProvider": "firstParty", "subscriptionType": "max"})); raise SystemExit(0)\n'
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "sys.stdin.read()\n"
        'child = subprocess.Popen([sys.executable, "-c", "import signal,time; '
        'signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])\n'
        f"pathlib.Path({str(pid_path)!r}).write_text(json.dumps([os.getpid(), child.pid]))\n"
        f"{tail}\n"
    )


def _pid_exists(pid: int) -> bool:
    if Path("/proc").is_dir():
        return Path(f"/proc/{pid}").exists()
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _wait_for_file(path: Path) -> None:
    for _ in range(500):
        if path.exists():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {path} to be written by the fake CLI")


async def _wait_for_pids_to_exit(pids: list[int]) -> None:
    for _ in range(300):
        if all(not _pid_exists(pid) for pid in pids):
            return
        await asyncio.sleep(0.01)


@pytest.fixture(autouse=True)
def _fast_grace(monkeypatch: pytest.MonkeyPatch):
    # Escalate SIGTERM -> SIGKILL quickly so the SIGTERM-ignoring descendant is
    # reaped without a long grace wait.
    monkeypatch.setattr(claude_process, "PROCESS_TERMINATE_GRACE_SECONDS", 0.25)
    claude_binary._VERIFIED_EXECUTABLES.clear()
    yield
    claude_binary._VERIFIED_EXECUTABLES.clear()


# --------------------------------------------------------------------------- #
# Group ownership on the normal path
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_successful_turn_leaves_process_group_empty(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    pid_path = tmp_path / "pids.json"
    fake = _write_executable(
        tmp_path / "claude", _fake_cli_forking(pid_path, parent_lingers=False)
    )

    runner = ClaudeProcessRunner(binary_path=fake, enforce_version=False)
    result = await runner.run(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert result.content == "lifecycle-ok"

    # The parent exited on its own; the SIGTERM-ignoring descendant must still be
    # reaped by the group kill during finalize.
    pids = json.loads(pid_path.read_text(encoding="utf-8"))
    await _wait_for_pids_to_exit(pids)
    assert all(not _pid_exists(pid) for pid in pids)


# --------------------------------------------------------------------------- #
# Cancellation tears down the whole tree
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_cancel_reaps_leader_and_descendant(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    pid_path = tmp_path / "pids.json"
    fake = _write_executable(
        tmp_path / "claude", _fake_cli_forking(pid_path, parent_lingers=True)
    )

    runner = ClaudeProcessRunner(binary_path=fake, enforce_version=False)
    task = asyncio.create_task(
        runner.run(messages=[{"role": "user", "content": "cancel"}], tools=[], timeout=30)
    )
    await _wait_for_file(pid_path)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    pids = json.loads(pid_path.read_text(encoding="utf-8"))
    await _wait_for_pids_to_exit(pids)
    assert all(not _pid_exists(pid) for pid in pids)


@pytest.mark.asyncio
async def test_double_cancel_still_reaps_and_propagates(monkeypatch, tmp_path):
    _patch_workspace(monkeypatch, tmp_path)
    pid_path = tmp_path / "pids.json"
    fake = _write_executable(
        tmp_path / "claude", _fake_cli_forking(pid_path, parent_lingers=True)
    )

    runner = ClaudeProcessRunner(binary_path=fake, enforce_version=False)
    task = asyncio.create_task(
        runner.run(messages=[{"role": "user", "content": "cancel"}], tools=[], timeout=30)
    )
    await _wait_for_file(pid_path)
    task.cancel()
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    pids = json.loads(pid_path.read_text(encoding="utf-8"))
    await _wait_for_pids_to_exit(pids)
    assert all(not _pid_exists(pid) for pid in pids)


# Cleanup-failure -> quarantine and the strict cross-turn blocking behavior are
# covered leak-free in test_claude_provider_quarantine.py.
