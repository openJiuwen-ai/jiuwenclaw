# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the per-conversation git snapshot behind the runtime prompt."""

from __future__ import annotations

import pytest

from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


def _make_adapter() -> JiuWenSwarmDeepAdapter:
    """Create a bare adapter carrying only the snapshot cache."""
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._session_git_snapshots = {}
    return adapter


def _make_runner(calls: list[list[str]], status: str = " M a.py") -> callable:
    """Return a git runner double recording commands and serving mutable status."""
    state = {"status": status}

    def _run(args: list[str]) -> str:
        calls.append(args)
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return "dev/branch"
        if args[0] == "status":
            return state["status"]
        if args[0] == "log":
            return "abc123 first"
        return ""

    _run.state = state
    return _run


def test_snapshot_is_taken_once_per_conversation() -> None:
    """Later turns reuse the first turn's snapshot instead of re-running git."""
    adapter = _make_adapter()
    calls: list[list[str]] = []
    runner = _make_runner(calls)

    first = adapter._resolve_session_git_snapshot("/repo", "sess_a", runner)
    for _ in range(4):
        adapter._resolve_session_git_snapshot("/repo", "sess_a", runner)

    assert first.branch == "dev/branch"
    assert first.status == " M a.py"
    assert first.recent_commits == "abc123 first"
    # branch + status + log on the first turn, nothing after.
    assert len(calls) == 3


def test_edits_during_the_conversation_do_not_change_the_snapshot() -> None:
    """The injected prompt promises a start-of-conversation snapshot.

    Re-reading it per turn is what rewrote the system prompt mid-conversation
    and invalidated the model's cached prefix.
    """
    adapter = _make_adapter()
    calls: list[list[str]] = []
    runner = _make_runner(calls)
    adapter._resolve_session_git_snapshot("/repo", "sess_a", runner)

    runner.state["status"] = " M a.py\n M b.py\n?? c.py"
    later = adapter._resolve_session_git_snapshot("/repo", "sess_a", runner)

    assert later.status == " M a.py"


def test_a_new_conversation_takes_a_fresh_snapshot() -> None:
    """A new session is a new conversation, so it re-reads git."""
    adapter = _make_adapter()
    calls: list[list[str]] = []
    runner = _make_runner(calls)
    adapter._resolve_session_git_snapshot("/repo", "sess_a", runner)
    runner.state["status"] = " M b.py"
    calls.clear()

    fresh = adapter._resolve_session_git_snapshot("/repo", "sess_b", runner)

    assert fresh.status == " M b.py"
    assert len(calls) == 3


def test_switching_project_dir_takes_a_fresh_snapshot() -> None:
    """Two project dirs in one session must not share a snapshot."""
    adapter = _make_adapter()
    calls: list[list[str]] = []
    runner = _make_runner(calls)
    adapter._resolve_session_git_snapshot("/repo/a", "sess_a", runner)
    calls.clear()

    adapter._resolve_session_git_snapshot("/repo/b", "sess_a", runner)

    assert len(calls) == 3


def test_status_is_capped_at_fifty_lines() -> None:
    """A large working tree must not flood the prompt."""
    adapter = _make_adapter()
    runner = _make_runner([], status="\n".join(f" M file{i}.py" for i in range(80)))

    snapshot = adapter._resolve_session_git_snapshot("/repo", "sess_a", runner)

    assert len(snapshot.status.splitlines()) == 50


def test_detached_head_falls_back_to_a_stable_name() -> None:
    """An empty branch read must not leave the prompt with a blank field."""
    adapter = _make_adapter()

    def _run(args: list[str]) -> str:
        return ""

    snapshot = adapter._resolve_session_git_snapshot("/repo", "sess_a", _run)

    assert snapshot.branch == "HEAD"


def test_snapshots_are_immutable() -> None:
    """A shared snapshot must not be editable by one caller."""
    adapter = _make_adapter()
    snapshot = adapter._resolve_session_git_snapshot("/repo", "sess_a", _make_runner([]))

    with pytest.raises(Exception):
        snapshot.status = "tampered"
