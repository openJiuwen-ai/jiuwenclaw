# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression: leader direct answer must finish the round when the team is settled.

Bug: openjiuwen never treats an empty task board as complete
(``is_team_completed`` returns None when there are no tasks), so after the
leader answered a trivial question directly the stream idled until the
relay-side stall watchdog cancelled it (OA.05000090).

Fix: ``_consume_stream_with_query_impl`` breaks out of the consume loop on
leader ``chat.final`` when ``_team_round_settled`` is true (settled/unstarted
members, terminal or empty tasks, no unread mail, no active workflow), then
runs normal teardown (``team.completed`` + ``pop_stream_task``) and acloses
the runner stream. Snapshot-based; does not rely on ``has_seen_team_events``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jiuwenclaw.agentserver.deep_agent import team_helpers


class _FakeMonitorHandler:
    """Returns a fixed team snapshot (pure DB-query analogue)."""

    def __init__(self, snapshot: dict | None, monitor=None) -> None:
        self._snapshot = snapshot
        self._monitor = monitor

    async def get_team_snapshot(self):
        return self._snapshot


class _FakeTeamManager:
    """Minimal TeamManager stand-in for the direct-answer path."""

    def __init__(self, snapshot: dict | None = None) -> None:
        self.events: list[dict] = []
        self._seen = False
        self.popped: list[str] = []
        self.cleared_pending: list[str] = []
        self.cleared_active: list[str] = []
        self._wf_handler = None
        # Default: no team / all idle. Pass members/tasks to simulate activity.
        self._handler = _FakeMonitorHandler(
            {"members": [], "tasks": []} if snapshot is None else snapshot
        )

    # --- seen_team_events / workflow tracking ---
    def reset_seen_team_events(self, session_id: str) -> None:
        self._seen = False

    def has_seen_team_events(self, session_id: str) -> bool:
        return self._seen

    def mark_seen_team_events(self, session_id: str) -> None:
        self._seen = True

    def reset_workflow_completed(self, session_id: str) -> None:
        pass

    def is_workflow_completed(self, session_id: str) -> bool:
        return False

    # --- broadcast ---
    def broadcast_event(self, session_id: str, event: dict) -> None:
        self.events.append(dict(event))

    # --- stream task lifecycle ---
    def pop_stream_task(self, session_id: str):
        self.popped.append(session_id)
        return None

    def clear_pending_runtime(self, session_id: str) -> None:
        self.cleared_pending.append(session_id)

    def clear_active_runtime(self, session_id: str, bookmark_paused: bool = False) -> None:
        self.cleared_active.append(session_id)

    def is_pause_in_progress(self, session_id: str) -> bool:
        return False

    def get_monitor_handler(self, session_id: str):
        return self._handler

    def get_workflow_handler(self, session_id: str):
        return self._wf_handler


def _make_hanging_stream(finalizer: list[str]):
    """Leader answers directly, then the stream goes silent forever (the bug)."""

    async def _gen(**kwargs):
        try:
            # dict chunk without role attr -> treated as leader output
            yield {"event_type": "chat.delta", "content": "你"}
            yield {"event_type": "chat.final", "content": "你好，我是产品设计负责人。"}
            # Simulate bug: runner goes silent after leader final.
            await asyncio.sleep(3600)
            yield {"event_type": "chat.delta", "content": "unreachable"}
        finally:
            finalizer.append("closed")

    return _gen


@pytest.mark.asyncio
async def test_leader_direct_answer_finishes_round(monkeypatch: pytest.MonkeyPatch) -> None:
    tm = _FakeTeamManager()
    finalizer: list[str] = []
    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id=None: tm)
    monkeypatch.delenv(team_helpers._STREAM_TRACE_ENV_KEY, raising=False)
    monkeypatch.setattr(
        team_helpers.Runner,
        "run_agent_team_streaming",
        _make_hanging_stream(finalizer),
    )
    team_spec = SimpleNamespace(team_name="oc_team_test", workspace=None)

    # Without the fix the consumer hangs on sleep and wait_for times out.
    await asyncio.wait_for(
        team_helpers._consume_stream_with_query_impl(
            "officeclaw",
            "sess-1",
            team_spec,
            "请介绍一下你自己",
            round_id=1,
            envs={},
            hide_dm=True,
        ),
        timeout=5,
    )

    # Normal teardown: team.completed broadcast and is_complete pushed.
    event_types = [e.get("event_type") for e in tm.events]
    assert "team.completed" in event_types
    assert any(
        e.get("event_type") == "chat.processing_status" and e.get("is_complete") is True
        for e in tm.events
    )
    # Stream task popped so the WS generator can exit and send is_final.
    assert tm.popped == ["sess-1"]
    # Runner stream explicitly closed in finally (not left for GC).
    assert finalizer == ["closed"]


def _make_final_then_silent_stream(finalizer: list[str]):
    """Leader answers (chat.final), then the stream goes silent forever."""

    async def _gen(**kwargs):
        try:
            yield {"event_type": "chat.delta", "content": "本轮结论"}
            yield {"event_type": "chat.final", "content": "游戏结束，user-researcher 被 @ 最多"}
            await asyncio.sleep(3600)
            yield {"event_type": "chat.delta", "content": "unreachable"}
        finally:
            finalizer.append("closed")

    return _gen


@pytest.mark.asyncio
async def test_busy_member_blocks_premature_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    """Busy member: leader chat.final must not finish the round."""
    tm = _FakeTeamManager(
        snapshot={
            "members": [{"member_id": "user-researcher", "status": "busy"}],
            "tasks": [],
        }
    )
    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id=None: tm)
    monkeypatch.delenv(team_helpers._STREAM_TRACE_ENV_KEY, raising=False)
    finalizer: list[str] = []
    monkeypatch.setattr(
        team_helpers.Runner,
        "run_agent_team_streaming",
        _make_final_then_silent_stream(finalizer),
    )
    team_spec = SimpleNamespace(team_name="oc_team_test", workspace=None)

    consumer = asyncio.create_task(
        team_helpers._consume_stream_with_query_impl(
            "officeclaw", "sess-busy", team_spec, "玩游戏",
            round_id=1, envs={}, hide_dm=True,
        )
    )
    await asyncio.sleep(1.0)
    assert not consumer.done(), "stream must not finish while a member is busy"
    assert not any(e.get("event_type") == "team.completed" for e in tm.events)

    consumer.cancel()
    try:
        await consumer
    except asyncio.CancelledError:
        pass
    assert finalizer == ["closed"]


@pytest.mark.asyncio
async def test_settled_members_allow_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    """All members idle and no tasks: leader chat.final finishes the round."""
    tm = _FakeTeamManager(
        snapshot={
            "members": [
                {"member_id": "user-researcher", "status": "ready"},
                {"member_id": "product-strategist", "status": "paused"},
            ],
            "tasks": [],
        }
    )
    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id=None: tm)
    monkeypatch.delenv(team_helpers._STREAM_TRACE_ENV_KEY, raising=False)
    finalizer: list[str] = []
    monkeypatch.setattr(
        team_helpers.Runner,
        "run_agent_team_streaming",
        _make_final_then_silent_stream(finalizer),
    )
    team_spec = SimpleNamespace(team_name="oc_team_test", workspace=None)

    await asyncio.wait_for(
        team_helpers._consume_stream_with_query_impl(
            "officeclaw", "sess-settled", team_spec, "玩个游戏",
            round_id=1, envs={}, hide_dm=True,
        ),
        timeout=5,
    )
    assert any(e.get("event_type") == "team.completed" for e in tm.events)
    assert tm.popped == ["sess-settled"]
    assert finalizer == ["closed"]


@pytest.mark.asyncio
async def test_open_task_blocks_premature_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    """Idle members with an open task: must not finish the round."""
    tm = _FakeTeamManager(
        snapshot={
            "members": [{"member_id": "user-researcher", "status": "ready"}],
            "tasks": [{"task_id": "t1", "status": "in_progress"}],
        }
    )
    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id=None: tm)
    monkeypatch.delenv(team_helpers._STREAM_TRACE_ENV_KEY, raising=False)
    finalizer: list[str] = []
    monkeypatch.setattr(
        team_helpers.Runner,
        "run_agent_team_streaming",
        _make_final_then_silent_stream(finalizer),
    )
    team_spec = SimpleNamespace(team_name="oc_team_test", workspace=None)

    consumer = asyncio.create_task(
        team_helpers._consume_stream_with_query_impl(
            "officeclaw", "sess-open-task", team_spec, "做需求",
            round_id=1, envs={}, hide_dm=True,
        )
    )
    await asyncio.sleep(1.0)
    assert not consumer.done(), "stream must not finish while tasks are open"
    assert not any(e.get("event_type") == "team.completed" for e in tm.events)

    consumer.cancel()
    try:
        await consumer
    except asyncio.CancelledError:
        pass


class _FakeMonitor:
    """Minimal openjiuwen TeamMonitor stand-in bound to a specific TeamAgent."""

    def __init__(self, agent) -> None:
        self._team_agent = agent
        self.team_name = "oc_team_test"
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def events(self):
        while not self.stopped:
            await asyncio.sleep(0.02)
        if False:
            yield None


@pytest.mark.asyncio
async def test_monitor_rebinds_when_pool_agent_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    """After pool CREATE replaces TeamAgent, rebind by stopping the stale monitor."""
    from jiuwenclaw.agentserver.team.handlers.team_monitor_handler import TeamMonitorHandler
    from jiuwenclaw.agentserver.team.team_manager import TeamManager

    tm = TeamManager()
    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id=None: tm)

    agent_a1 = object()
    agent_a2 = object()
    stale = TeamMonitorHandler(_FakeMonitor(agent_a1), "sess-m")
    await stale.start()
    tm.register_monitor("sess-m", stale)

    created: list[_FakeMonitor] = []

    async def _fake_get_monitor(team_name=None, session_id=None, hide_dm=False):
        monitor = _FakeMonitor(agent_a2)
        created.append(monitor)
        return monitor

    async def _fake_pool_agent(team_name):
        return agent_a2

    monkeypatch.setattr(team_helpers.Runner, "get_agent_team_monitor", _fake_get_monitor)
    monkeypatch.setattr(team_helpers, "_current_pool_team_agent", _fake_pool_agent)

    await team_helpers.ensure_monitor_handlers_for_active_runtime(
        "officeclaw", "sess-m", "oc_team_test"
    )

    current = tm.get_monitor("sess-m")
    assert stale._monitor.stopped is True, "stale monitor must be stopped"
    assert current is not stale, "monitor must be rebound"
    assert len(created) == 1
    assert current._monitor._team_agent is agent_a2

    await current.stop()


@pytest.mark.asyncio
async def test_monitor_kept_when_bound_agent_is_current(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not rebuild the monitor when the bound agent is still current."""
    from jiuwenclaw.agentserver.team.handlers.team_monitor_handler import TeamMonitorHandler
    from jiuwenclaw.agentserver.team.team_manager import TeamManager

    tm = TeamManager()
    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id=None: tm)

    agent = object()
    handler = TeamMonitorHandler(_FakeMonitor(agent), "sess-k")
    await handler.start()
    tm.register_monitor("sess-k", handler)

    async def _fake_get_monitor(team_name=None, session_id=None, hide_dm=False):
        raise AssertionError("must not create a new monitor")

    async def _fake_pool_agent(team_name):
        return agent

    monkeypatch.setattr(team_helpers.Runner, "get_agent_team_monitor", _fake_get_monitor)
    monkeypatch.setattr(team_helpers, "_current_pool_team_agent", _fake_pool_agent)

    await team_helpers.ensure_monitor_handlers_for_active_runtime(
        "officeclaw", "sess-k", "oc_team_test"
    )

    assert tm.get_monitor("sess-k") is handler
    assert handler._monitor.stopped is False

    await handler.stop()


# ---------------------------------------------------------------------------
# Incident: in-round build_team registers members that never spawn
# (status=unstarted, empty board). Leader chat.final arrives but the old
# settle check rejected unstarted forever -> stream hung until relay watchdog.
# ---------------------------------------------------------------------------


def _make_unstarted_snapshot() -> dict:
    return {
        "members": [
            {"member_id": name, "status": "unstarted"}
            for name in (
                "product-architect",
                "client-engineer",
                "server-engineer",
                "quality-engineer",
                "release-engineer",
            )
        ],
        "tasks": [],
    }


def _make_handler_with_unread(snapshot: dict, unread: bool):
    """Fake monitor handler whose backend reports a fixed unread state."""

    async def _has_unread(include_broadcast: bool = True) -> bool:
        return unread

    backend = SimpleNamespace(
        message_manager=SimpleNamespace(has_unread_messages=_has_unread)
    )
    monitor = SimpleNamespace(_team_agent=SimpleNamespace(team_backend=backend))
    return _FakeMonitorHandler(snapshot, monitor=monitor)


@pytest.mark.asyncio
async def test_unstarted_members_empty_board_allow_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unstarted members, empty board, no unread: leader chat.final must finish."""
    tm = _FakeTeamManager()
    tm._handler = _make_handler_with_unread(_make_unstarted_snapshot(), unread=False)
    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id=None: tm)
    monkeypatch.delenv(team_helpers._STREAM_TRACE_ENV_KEY, raising=False)
    finalizer: list[str] = []
    monkeypatch.setattr(
        team_helpers.Runner,
        "run_agent_team_streaming",
        _make_final_then_silent_stream(finalizer),
    )
    team_spec = SimpleNamespace(team_name="oc_team_test", workspace=None)

    await asyncio.wait_for(
        team_helpers._consume_stream_with_query_impl(
            "officeclaw", "sess-unstarted", team_spec, "请介绍一下你们团队",
            round_id=1, envs={}, hide_dm=True,
        ),
        timeout=5,
    )
    assert any(e.get("event_type") == "team.completed" for e in tm.events)
    assert tm.popped == ["sess-unstarted"]
    assert finalizer == ["closed"]


@pytest.mark.asyncio
async def test_unstarted_member_with_unread_message_blocks_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unread mail to unspawned members (no tasks): must not finish."""
    tm = _FakeTeamManager()
    tm._handler = _make_handler_with_unread(_make_unstarted_snapshot(), unread=True)
    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id=None: tm)
    monkeypatch.delenv(team_helpers._STREAM_TRACE_ENV_KEY, raising=False)
    finalizer: list[str] = []
    monkeypatch.setattr(
        team_helpers.Runner,
        "run_agent_team_streaming",
        _make_final_then_silent_stream(finalizer),
    )
    team_spec = SimpleNamespace(team_name="oc_team_test", workspace=None)

    consumer = asyncio.create_task(
        team_helpers._consume_stream_with_query_impl(
            "officeclaw", "sess-unread", team_spec, "介绍一下团队并通知成员",
            round_id=1, envs={}, hide_dm=True,
        )
    )
    await asyncio.sleep(1.0)
    assert not consumer.done(), "stream must not finish while unread mail exists"
    assert not any(e.get("event_type") == "team.completed" for e in tm.events)

    consumer.cancel()
    try:
        await consumer
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_open_task_with_unstarted_member_blocks_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unstarted members plus an open task: must not finish."""
    tm = _FakeTeamManager(
        snapshot={
            "members": [{"member_id": "server-engineer", "status": "unstarted"}],
            "tasks": [{"task_id": "t1", "status": "pending"}],
        }
    )
    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id=None: tm)
    monkeypatch.delenv(team_helpers._STREAM_TRACE_ENV_KEY, raising=False)
    finalizer: list[str] = []
    monkeypatch.setattr(
        team_helpers.Runner,
        "run_agent_team_streaming",
        _make_final_then_silent_stream(finalizer),
    )
    team_spec = SimpleNamespace(team_name="oc_team_test", workspace=None)

    consumer = asyncio.create_task(
        team_helpers._consume_stream_with_query_impl(
            "officeclaw", "sess-unstarted-task", team_spec, "做需求",
            round_id=1, envs={}, hide_dm=True,
        )
    )
    await asyncio.sleep(1.0)
    assert not consumer.done(), "stream must not finish while tasks are open"
    assert not any(e.get("event_type") == "team.completed" for e in tm.events)

    consumer.cancel()
    try:
        await consumer
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_active_workflow_blocks_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    """Active swarmflow on leader: leader chat.final must not finish."""
    tm = _FakeTeamManager()
    tm._handler = _make_handler_with_unread(_make_unstarted_snapshot(), unread=False)
    running_run = SimpleNamespace(is_terminal=lambda: False)
    tm._wf_handler = SimpleNamespace(get_run_states=lambda: {"run-1": running_run})
    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id=None: tm)
    monkeypatch.delenv(team_helpers._STREAM_TRACE_ENV_KEY, raising=False)
    finalizer: list[str] = []
    monkeypatch.setattr(
        team_helpers.Runner,
        "run_agent_team_streaming",
        _make_final_then_silent_stream(finalizer),
    )
    team_spec = SimpleNamespace(team_name="oc_team_test", workspace=None)

    consumer = asyncio.create_task(
        team_helpers._consume_stream_with_query_impl(
            "officeclaw", "sess-wf-active", team_spec, "跑个流程",
            round_id=1, envs={}, hide_dm=True,
        )
    )
    await asyncio.sleep(1.0)
    assert not consumer.done(), "stream must not finish while a workflow is active"
    assert not any(e.get("event_type") == "team.completed" for e in tm.events)

    consumer.cancel()
    try:
        await consumer
    except asyncio.CancelledError:
        pass
