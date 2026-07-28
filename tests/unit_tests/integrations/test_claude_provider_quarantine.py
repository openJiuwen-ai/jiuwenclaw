"""Strict cross-turn quarantine tests (Decision 1).

Leak-free by construction: every long-lived process here is one this test spawns,
owns, and kills in a ``finally``. The runner-level reap-failure test uses a
NON-forking fake CLI whose processes exit on their own, so injecting a cleanup
failure records a marker for an already-dead group without leaking anything.

Covers:
* the marker round-trip: a genuinely alive group blocks reconcile; a dead group
  clears it;
* tampered/corrupt markers fail closed;
* the runner records a quarantine and raises ``provider_unavailable`` when a
  child group cannot be confirmed reaped;
* a subsequent turn is BLOCKED while a recorded group is still alive, and runs
  again once that group is proven gone.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from jiuwenswarm.integrations.ai4research_subscription import claude_binary, claude_process
from jiuwenswarm.integrations.ai4research_subscription.claude_process import (
    ClaudeProcessRunner,
    ensure_claude_runtime,
)
from jiuwenswarm.integrations.ai4research_subscription import claude_quarantine
from jiuwenswarm.integrations.ai4research_subscription.claude_quarantine import (
    _marker_path,
    record_uncertain_groups,
    reconcile_claude_quarantine,
    reset_quarantine_for_tests,
)
from jiuwenswarm.integrations.ai4research_subscription.errors import ClaudeProviderError

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="process-group quarantine is POSIX-specific"
)


def _patch_workspace(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "instance"
    workspace.mkdir(mode=0o700)
    monkeypatch.setattr(claude_process, "get_user_workspace_dir", lambda: workspace)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return ensure_claude_runtime()


def _spawn_owned_group() -> subprocess.Popen:
    """A real, session-leader child (pgid == pid) that sleeps until killed."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _wait_group_gone(pgid: int) -> None:
    for _ in range(300):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)


@pytest.fixture(autouse=True)
def _clear_version_cache():
    claude_binary._VERIFIED_EXECUTABLES.clear()
    reset_quarantine_for_tests()
    yield
    claude_binary._VERIFIED_EXECUTABLES.clear()
    reset_quarantine_for_tests()


# --------------------------------------------------------------------------- #
# Quarantine module
# --------------------------------------------------------------------------- #

def test_reconcile_clears_when_group_is_gone(monkeypatch, tmp_path):
    runtime = _patch_workspace(monkeypatch, tmp_path)
    proc = _spawn_owned_group()
    pgid = proc.pid
    _kill_group(proc)
    _wait_group_gone(pgid)
    record_uncertain_groups(runtime.root, [pgid])
    assert _marker_path(runtime.root).exists()
    reconcile_claude_quarantine(runtime.root)  # gone -> clears
    assert not _marker_path(runtime.root).exists()


def test_reconcile_blocks_while_group_alive_then_clears(monkeypatch, tmp_path):
    runtime = _patch_workspace(monkeypatch, tmp_path)
    proc = _spawn_owned_group()
    try:
        record_uncertain_groups(runtime.root, [proc.pid])
        with pytest.raises(ClaudeProviderError) as exc:
            reconcile_claude_quarantine(runtime.root)
        assert exc.value.code == "provider_unavailable"
        assert _marker_path(runtime.root).exists()  # still quarantined
    finally:
        _kill_group(proc)
    _wait_group_gone(proc.pid)
    reconcile_claude_quarantine(runtime.root)  # now gone -> clears
    assert not _marker_path(runtime.root).exists()


def test_corrupt_marker_fails_closed(monkeypatch, tmp_path):
    runtime = _patch_workspace(monkeypatch, tmp_path)
    _marker_path(runtime.root).write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ClaudeProviderError) as exc:
        reconcile_claude_quarantine(runtime.root)
    assert exc.value.code == "provider_unavailable"


def test_symlink_marker_fails_closed(monkeypatch, tmp_path):
    runtime = _patch_workspace(monkeypatch, tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"process_groups": []}), encoding="utf-8")
    _marker_path(runtime.root).symlink_to(outside)
    with pytest.raises(ClaudeProviderError) as exc:
        reconcile_claude_quarantine(runtime.root)
    assert exc.value.code == "provider_unavailable"


def test_record_merges_groups(monkeypatch, tmp_path):
    runtime = _patch_workspace(monkeypatch, tmp_path)
    record_uncertain_groups(runtime.root, [111])
    record_uncertain_groups(runtime.root, [222, 111])
    document = json.loads(_marker_path(runtime.root).read_text(encoding="utf-8"))
    assert sorted(document["process_groups"]) == [111, 222]


def test_marker_write_failure_still_blocks(monkeypatch, tmp_path):
    """Fail-closed: if the marker file cannot be written, the in-process record
    still blocks a subsequent turn (never fail open)."""
    runtime = _patch_workspace(monkeypatch, tmp_path)
    monkeypatch.setattr(claude_quarantine, "_write_marker", lambda root, groups: None)
    proc = _spawn_owned_group()
    try:
        record_uncertain_groups(runtime.root, [proc.pid])
        assert not _marker_path(runtime.root).exists()  # write was suppressed
        with pytest.raises(ClaudeProviderError) as exc:
            reconcile_claude_quarantine(runtime.root)  # in-process set blocks
        assert exc.value.code == "provider_unavailable"
    finally:
        _kill_group(proc)
    _wait_group_gone(proc.pid)
    reconcile_claude_quarantine(runtime.root)  # gone -> clears in-process record


# --------------------------------------------------------------------------- #
# Adversarial: persistence failures and concurrency
# --------------------------------------------------------------------------- #

def _impossible_pgid_base() -> int:
    """A pgid value the kernel can never assign (above pid_max), so it is
    always provably gone yet still a positive int the recorder accepts."""
    try:
        return int(Path("/proc/sys/kernel/pid_max").read_text()) + 10_000
    except OSError:
        return 2**22 + 10_000


def test_concurrent_records_lose_nothing(monkeypatch, tmp_path):
    """16 threads recording disjoint groups concurrently: every group survives
    in the marker, and reconcile clears them all once proven gone."""
    runtime = _patch_workspace(monkeypatch, tmp_path)
    base = _impossible_pgid_base()
    per_thread = [[base + t * 8 + i for i in range(8)] for t in range(16)]
    threads = [
        threading.Thread(target=record_uncertain_groups, args=(runtime.root, groups))
        for groups in per_thread
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    document = json.loads(_marker_path(runtime.root).read_text(encoding="utf-8"))
    expected = sorted(pgid for groups in per_thread for pgid in groups)
    assert sorted(document["process_groups"]) == expected
    reconcile_claude_quarantine(runtime.root)  # all impossible pgids are gone
    assert not _marker_path(runtime.root).exists()


def test_concurrent_record_and_reconcile_never_fail_open(monkeypatch, tmp_path):
    """Reconcile hammered from several threads while records land concurrently
    must never clear the quarantine while the recorded group is still alive."""
    runtime = _patch_workspace(monkeypatch, tmp_path)
    base = _impossible_pgid_base()
    proc = _spawn_owned_group()
    failures: list[str] = []
    try:
        record_uncertain_groups(runtime.root, [proc.pid])

        def hammer_reconcile() -> None:
            for _ in range(25):
                try:
                    reconcile_claude_quarantine(runtime.root)
                    failures.append("reconcile cleared while the group was alive")
                except ClaudeProviderError:
                    pass

        def hammer_record(offset: int) -> None:
            for i in range(25):
                record_uncertain_groups(runtime.root, [base + offset + i])

        threads = [threading.Thread(target=hammer_reconcile) for _ in range(4)] + [
            threading.Thread(target=hammer_record, args=(1000 * (n + 1),)) for n in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not failures
        document = json.loads(_marker_path(runtime.root).read_text(encoding="utf-8"))
        assert proc.pid in document["process_groups"]
        with pytest.raises(ClaudeProviderError):
            reconcile_claude_quarantine(runtime.root)
    finally:
        _kill_group(proc)
    _wait_group_gone(proc.pid)
    reconcile_claude_quarantine(runtime.root)
    assert not _marker_path(runtime.root).exists()


def test_unlink_failure_keeps_blocking(monkeypatch, tmp_path):
    """If clearing the marker fails (read-only directory), reconcile raises and
    the marker survives, so the NEXT turn is still blocked - never fail open."""
    if os.geteuid() == 0:
        pytest.skip("directory permissions do not bind root")
    runtime = _patch_workspace(monkeypatch, tmp_path)
    proc = _spawn_owned_group()
    pgid = proc.pid
    _kill_group(proc)
    _wait_group_gone(pgid)
    record_uncertain_groups(runtime.root, [pgid])
    os.chmod(runtime.root, 0o500)
    try:
        with pytest.raises(ClaudeProviderError) as exc:
            reconcile_claude_quarantine(runtime.root)
        assert exc.value.code == "provider_unavailable"
        assert _marker_path(runtime.root).exists()
        with pytest.raises(ClaudeProviderError):
            reconcile_claude_quarantine(runtime.root)  # still blocked next turn
    finally:
        os.chmod(runtime.root, 0o700)
    reconcile_claude_quarantine(runtime.root)
    assert not _marker_path(runtime.root).exists()


def test_unreadable_marker_fails_closed(monkeypatch, tmp_path):
    if os.geteuid() == 0:
        pytest.skip("file permissions do not bind root")
    runtime = _patch_workspace(monkeypatch, tmp_path)
    marker = _marker_path(runtime.root)
    marker.write_text(json.dumps({"process_groups": []}), encoding="utf-8")
    os.chmod(marker, 0o000)
    try:
        with pytest.raises(ClaudeProviderError) as exc:
            reconcile_claude_quarantine(runtime.root)
        assert exc.value.code == "provider_unavailable"
    finally:
        os.chmod(marker, 0o600)


def test_boolean_pgid_tampering_fails_closed(monkeypatch, tmp_path):
    """JSON true/false are ints in Python; the marker must reject them."""
    runtime = _patch_workspace(monkeypatch, tmp_path)
    _marker_path(runtime.root).write_text(
        json.dumps({"process_groups": [True]}), encoding="utf-8"
    )
    with pytest.raises(ClaudeProviderError) as exc:
        reconcile_claude_quarantine(runtime.root)
    assert exc.value.code == "provider_unavailable"


def test_nonlist_groups_tampering_fails_closed(monkeypatch, tmp_path):
    runtime = _patch_workspace(monkeypatch, tmp_path)
    _marker_path(runtime.root).write_text(
        json.dumps({"process_groups": {"pgid": 123}}), encoding="utf-8"
    )
    with pytest.raises(ClaudeProviderError) as exc:
        reconcile_claude_quarantine(runtime.root)
    assert exc.value.code == "provider_unavailable"


def test_nonpositive_pgids_block_and_are_never_signalled(monkeypatch, tmp_path):
    """A tampered marker holding 0 or -1 must block the turn WITHOUT ever
    calling killpg on a non-positive pgid (which would signal our own group
    or every process we can reach)."""
    runtime = _patch_workspace(monkeypatch, tmp_path)
    _marker_path(runtime.root).write_text(
        json.dumps({"process_groups": [0, -1]}), encoding="utf-8"
    )
    signalled: list[int] = []
    real_killpg = os.killpg

    def spy(pgid, sig):
        signalled.append(pgid)
        return real_killpg(pgid, sig)

    monkeypatch.setattr(os, "killpg", spy)
    with pytest.raises(ClaudeProviderError) as exc:
        reconcile_claude_quarantine(runtime.root)
    assert exc.value.code == "provider_unavailable"
    assert signalled == []


# --------------------------------------------------------------------------- #
# Runner integration
# --------------------------------------------------------------------------- #

_SUBSCRIPTION_AUTH = (
    '{"loggedIn":true,"authMethod":"claude.ai","apiProvider":"firstParty","subscriptionType":"max"}'
)
_SUCCESS_INFERENCE = json.dumps(
    {
        "is_error": False,
        "num_turns": 1,
        "result": json.dumps(
            {"content": "answer", "reasoning_content": "", "tool_calls": [], "finish_reason": "stop"}
        ),
    }
)


def _write_nonforking_cli(path: Path) -> Path:
    """A subscription-authenticated CLI whose processes exit on their own."""
    script = (
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "2.1.218 (Claude Code)"; exit 0; fi\n'
        f'if [ "$1" = "auth" ]; then echo \'{_SUBSCRIPTION_AUTH}\'; exit 0; fi\n'
        "cat >/dev/null\n"
        f"cat <<'EOF'\n{_SUCCESS_INFERENCE}\nEOF\n"
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(0o700)
    return path


@pytest.mark.asyncio
async def test_runner_records_quarantine_on_reap_failure(monkeypatch, tmp_path):
    runtime = _patch_workspace(monkeypatch, tmp_path)
    binary = _write_nonforking_cli(tmp_path / "claude")
    runner = ClaudeProcessRunner(binary_path=binary, enforce_version=False)

    async def _fail_reap(*_args, **_kwargs):
        raise RuntimeError("injected unreapable group")

    with monkeypatch.context() as scoped:
        scoped.setattr(claude_process, "terminate_process_group", _fail_reap)
        with pytest.raises(ClaudeProviderError) as exc:
            await runner.run(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert exc.value.code == "provider_unavailable"
    # A marker was recorded for the (now already-exited) child group.
    document = json.loads(_marker_path(runtime.root).read_text(encoding="utf-8"))
    assert document["process_groups"]  # non-empty


@pytest.mark.asyncio
async def test_version_probe_reap_failure_records_quarantine(monkeypatch, tmp_path):
    """Finding 2: a version-probe child that can't be reaped is quarantined."""
    runtime = _patch_workspace(monkeypatch, tmp_path)
    binary = _write_nonforking_cli(tmp_path / "claude")
    runner = ClaudeProcessRunner(binary_path=binary, enforce_version=True)

    async def _fail_reap(*_args, **_kwargs):
        raise RuntimeError("injected unreapable version probe")

    with monkeypatch.context() as scoped:
        scoped.setattr(claude_binary, "terminate_process_group", _fail_reap)
        with pytest.raises(ClaudeProviderError) as exc:
            await runner.run(messages=[{"role": "user", "content": "hi"}], tools=[])
    assert exc.value.code == "provider_unavailable"
    document = json.loads(_marker_path(runtime.root).read_text(encoding="utf-8"))
    assert document["process_groups"]  # the version-probe group was recorded


@pytest.mark.asyncio
async def test_runner_blocked_until_recorded_group_gone(monkeypatch, tmp_path):
    runtime = _patch_workspace(monkeypatch, tmp_path)
    binary = _write_nonforking_cli(tmp_path / "claude")
    runner = ClaudeProcessRunner(binary_path=binary, enforce_version=False)

    proc = _spawn_owned_group()
    try:
        record_uncertain_groups(runtime.root, [proc.pid])
        # A live recorded group blocks the turn at reconcile - before any spawn.
        with pytest.raises(ClaudeProviderError) as exc:
            await runner.run(messages=[{"role": "user", "content": "blocked"}], tools=[])
        assert exc.value.code == "provider_unavailable"
    finally:
        _kill_group(proc)
    _wait_group_gone(proc.pid)

    # Group proven gone -> quarantine clears and the next turn runs.
    result = await runner.run(messages=[{"role": "user", "content": "now ok"}], tools=[])
    assert result.content == "answer"
    assert not _marker_path(runtime.root).exists()
