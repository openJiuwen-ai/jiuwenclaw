# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task 2: per-request send_file_request_context binding in team run.

Team mode never bound ``_send_file_request_context`` (only skill_turbo did),
so SendFileToolkit._resolve_route fell back to the global-singleton instance
fields (overwritten by concurrent team runs). Binding the contextvar per team
run makes routing concurrency-safe (ContextVar is per-async-task isolated).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
    get_send_file_request_context,
)


def _request(session_id="officeclaw_s1", request_id="req-1", channel_id="officeclaw"):
    r = MagicMock()
    r.session_id = session_id
    r.request_id = request_id
    r.channel_id = channel_id
    r.params = {}
    r.metadata = None
    return r


def _mock_team_manager(capture_list):
    tm = MagicMock()
    tm.wait_for_pause_complete = None
    tm.has_stream_task = MagicMock(return_value=False)
    tm.has_waiters = MagicMock(return_value=False)
    tm.is_session_initialized = MagicMock(return_value=True)  # follow-up path
    tm.interact = AsyncMock(return_value=(True, "ok"))
    tm.remember_user_query = MagicMock()
    tm.get_last_user_query = MagicMock(return_value=None)
    tm.get_paused_team_name = MagicMock(return_value="paused")
    tm.remove_waiter = MagicMock()
    tm.clear_session_initialized = MagicMock()
    tm.ensure_team_shared_skills_ready_for_session = MagicMock()
    tm.get_swarm_enriched_team_spec = AsyncMock(return_value=MagicMock())
    tm.set_stream_task = MagicMock()
    tm.clear_stream_task = MagicMock()
    tm.reset_seen_team_events = MagicMock()
    tm.reset_workflow_completed = MagicMock()

    def add_waiter(sid, rid, q):
        # Called inside try@1592 (follow-up path) AFTER the contextvar is bound.
        ctx = get_send_file_request_context()
        capture_list.append(None if ctx is None else dict(ctx))
        q.put_nowait({"event_type": "team.completed"})

    tm.add_waiter = add_waiter
    return tm


def _patch_heavy(monkeypatch, tm):
    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    monkeypatch.setattr(th, "get_team_manager", lambda cid: tm)
    monkeypatch.setattr(th, "_resolve_request_language", lambda r: "cn")
    monkeypatch.setattr(th, "_normalize_team_query", lambda q, **k: q)
    monkeypatch.setattr(th, "_detect_resume_from_pause", AsyncMock(return_value=False))
    monkeypatch.setattr(th, "_resolve_team_project_dir", lambda r: None)
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=None)
    cm.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(th, "agent_teams_home_scope", lambda pd: cm)
    import jiuwenclaw.agentserver.team.remote_member_bootstrap as rmb
    monkeypatch.setattr(
        rmb, "wait_for_pending_shutdown_cleanup_for_session", AsyncMock(return_value=None)
    )


async def _drive(monkeypatch, session_id, request_id):
    capture: list = []
    tm = _mock_team_manager(capture)
    _patch_heavy(monkeypatch, tm)
    import jiuwenclaw.agentserver.deep_agent.team_helpers as th

    async for _ in th.process_team_message_stream(
        _request(session_id, request_id), {"query": "q"}, MagicMock()
    ):
        pass
    return capture


@pytest.mark.asyncio
async def test_send_file_context_bound_during_team_run(monkeypatch):
    assert get_send_file_request_context() is None  # before run
    capture = await _drive(monkeypatch, "officeclaw_s1", "req-1")
    assert capture, "add_waiter was not reached"
    ctx = capture[0]
    assert ctx is not None, "contextvar was not bound during the run"
    assert ctx["request_id"] == "req-1"
    assert ctx["session_id"] == "officeclaw_s1"
    assert ctx["channel_id"] == "officeclaw"
    assert get_send_file_request_context() is None  # reset after run


@pytest.mark.asyncio
async def test_sequential_runs_do_not_leak_context(monkeypatch):
    """Run 2 must not see run 1's leftover contextvar (reset works)."""
    cap1 = await _drive(monkeypatch, "officeclaw_s1", "req-1")
    cap2 = await _drive(monkeypatch, "officeclaw_s2", "req-2")
    assert cap1[0]["request_id"] == "req-1"
    assert cap2[0]["request_id"] == "req-2"  # not req-1 → no leak


@pytest.mark.asyncio
async def test_contextvar_isolates_concurrent_async_tasks():
    """ContextVar (the mechanism this binding relies on) is per-task isolated."""
    from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
        reset_send_file_request_context,
        set_send_file_request_context,
    )

    seen: dict = {}

    async def worker(tag, rid):
        token = set_send_file_request_context(
            request_id=rid, session_id=tag, channel_id="officeclaw"
        )
        # Yield to interleave with the other task; each must still see its own ctx.
        await asyncio.sleep(0.01)
        ctx = get_send_file_request_context() or {}
        seen[tag] = ctx.get("request_id")
        reset_send_file_request_context(token)

    await asyncio.gather(
        worker("s1", "req-1"),
        worker("s2", "req-2"),
    )
    assert seen["s1"] == "req-1"
    assert seen["s2"] == "req-2"
