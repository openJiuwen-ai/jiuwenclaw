# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team evolution monitor helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from openjiuwen.agent_teams.schema.team import TeamRole

from jiuwenswarm.agents.harness.team import TeamMonitorHandler
from jiuwenswarm.server.runtime.agent_adapter import team_helpers


class _FakeTransport:
    pushes: list[dict] = []

    def __init__(self):
        self.pushes = self.__class__.pushes

    async def send_push(self, payload: dict) -> None:
        self.pushes.append(payload)


class _FakeRail:
    def __init__(self, batches: list[list[object]], *, pending_first: bool = True):
        self._batches = list(batches)
        self._pending_first = pending_first
        self._drain_calls = 0
        self.drain_waits: list[bool] = []

    async def drain_pending_approval_events(self, wait: bool = False, timeout: float | None = None):
        self._drain_calls += 1
        self.drain_waits.append(wait)
        if self._batches:
            return self._batches.pop(0)
        return []


class _FakeProgressOnlyRail(_FakeRail):
    def __init__(self):
        super().__init__(
            [
                [
                    SimpleNamespace(
                        type="llm_reasoning",
                        payload={
                            "request_id": "team_skill_evolve_timeout",
                            "content": "[Team Skill Evolution] progress",
                        },
                    )
                ],
            ],
            pending_first=False,
        )
        self.cleanup_calls = 0

    async def cleanup_background_tasks(self) -> None:
        self.cleanup_calls += 1


class _TeamHelpersTestApi:
    @staticmethod
    async def watch_team_evolution_and_push(
        channel_id: str | None,
        session_id: str,
        rail: object,
    ) -> None:
        watcher = getattr(team_helpers, "_watch_team_evolution_and_push")
        await watcher(channel_id, session_id, rail)

    @staticmethod
    def ensure_team_evolution_watcher(
        channel_id: str | None,
        session_id: str,
        *,
        source: str = "unknown",
    ) -> None:
        ensure_watcher = getattr(team_helpers, "ensure_team_evolution_watcher")
        ensure_watcher(channel_id, session_id, source=source)

    @staticmethod
    async def handle_team_evolve_list_command(
        channel_id: str | None,
        session_id: str,
        query: str,
    ) -> dict[str, object] | None:
        handler = getattr(team_helpers, "_handle_team_evolve_list_command")
        return await handler(channel_id, session_id, query)

    @staticmethod
    async def handle_team_slash_command(
        channel_id: str | None,
        session_id: str,
        query: str,
    ) -> dict[str, object] | None:
        handler = getattr(team_helpers, "_handle_team_slash_command")
        return await handler(channel_id, session_id, query)

    @staticmethod
    async def consume_stream_with_query(
        channel_id: str | None,
        session_id: str,
        spec: object,
        query: str,
    ) -> None:
        consumer = getattr(team_helpers, "_consume_stream_with_query")
        await consumer(channel_id, session_id, spec, query)

    @staticmethod
    async def consume_monitor_events(
        channel_id: str | None,
        session_id: str,
        monitor_handler: object,
    ) -> None:
        consumer = getattr(team_helpers, "_consume_monitor_events")
        await consumer(channel_id, session_id, monitor_handler)


@pytest.mark.anyio
async def test_team_evolution_monitor_pushes_status_with_real_request_id(monkeypatch):
    _FakeTransport.pushes = []
    approval_event = SimpleNamespace(
        type="chat.ask_user_question",
        payload={"request_id": "team_skill_evolve_req1", "questions": [{"header": "x"}]},
    )
    reasoning_event = SimpleNamespace(
        type="llm_reasoning",
        payload={"content": "[Team Skill Evolution] started"},
    )
    rail = _FakeRail([[reasoning_event, approval_event]], pending_first=False)

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )
    monkeypatch.setattr(
        team_helpers,
        "parse_stream_chunk",
        lambda evt: {"event_type": "chat.reasoning", "content": evt.payload.get("content", "")},
    )

    task = asyncio.create_task(
        _TeamHelpersTestApi.watch_team_evolution_and_push("web", "sess-1", rail)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    event_types = [push["payload"]["event_type"] for push in _FakeTransport.pushes]
    assert event_types == [
        "chat.evolution_status",
        "chat.ask_user_question",
        "chat.evolution_status",
    ]
    assert _FakeTransport.pushes[0]["request_id"] == "team_skill_evolve_req1"
    assert _FakeTransport.pushes[0]["payload"]["request_id"] == "team_skill_evolve_req1"
    assert _FakeTransport.pushes[2]["payload"]["status"] == "end"
    assert _FakeTransport.pushes[2]["payload"]["stage"] == "approval_required"
    assert rail.drain_waits
    assert set(rail.drain_waits) == {False}


@pytest.mark.anyio
async def test_team_evolution_monitor_waits_for_real_request_id(monkeypatch):
    _FakeTransport.pushes = []
    reasoning_event = SimpleNamespace(
        type="llm_reasoning",
        payload={"content": "[Team Skill Evolution] started"},
    )
    approval_event = SimpleNamespace(
        type="chat.ask_user_question",
        payload={"request_id": "team_skill_evolve_real", "questions": [{"header": "x"}]},
    )
    rail = _FakeRail([[reasoning_event], [approval_event]], pending_first=False)

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )
    monkeypatch.setattr(
        team_helpers,
        "parse_stream_chunk",
        lambda evt: {"event_type": "chat.reasoning", "content": evt.payload.get("content", "")},
    )

    task = asyncio.create_task(
        _TeamHelpersTestApi.watch_team_evolution_and_push("web", "sess-1", rail)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    status_pushes = [
        push for push in _FakeTransport.pushes
        if push["payload"]["event_type"] == "chat.evolution_status"
    ]
    assert [push["payload"]["status"] for push in status_pushes] == ["start", "end"]
    assert [push["request_id"] for push in status_pushes] == [
        "team_skill_evolve_real",
        "team_skill_evolve_real",
    ]


@pytest.mark.anyio
async def test_team_evolution_monitor_starts_cycle_for_started_progress_without_request_id(
    monkeypatch,
):
    _FakeTransport.pushes = []
    progress_event = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {"event_kind": "progress", "stage": "started"},
            "content": "[Team Skill Evolution] started",
        },
    )
    outcome_event = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {"event_kind": "outcome", "status": "failed"},
            "content": "failed before approval",
        },
    )
    rail = _FakeRail([[progress_event], [outcome_event]], pending_first=False)

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )
    monkeypatch.setattr(team_helpers, "parse_stream_chunk", lambda evt: None)

    task = asyncio.create_task(
        _TeamHelpersTestApi.watch_team_evolution_and_push("web", "sess-progress", rail)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    status_pushes = [
        push for push in _FakeTransport.pushes
        if push["payload"]["event_type"] == "chat.evolution_status"
    ]
    assert status_pushes == []


@pytest.mark.anyio
async def test_team_evolution_monitor_maps_sdk_progress_stages(monkeypatch):
    _FakeTransport.pushes = []
    detecting_event = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {
                "event_kind": "progress",
                "stage": "detecting_signals",
                "request_id": "team_skill_evolve_stages",
            },
            "request_id": "team_skill_evolve_stages",
            "content": "[Team Skill Evolution] detecting",
        },
    )
    generating_event = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {
                "event_kind": "progress",
                "stage": "generating_updates",
                "request_id": "team_skill_evolve_stages",
            },
            "request_id": "team_skill_evolve_stages",
            "content": "[Team Skill Evolution] generating",
        },
    )
    outcome_event = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {"event_kind": "outcome", "status": "completed"},
            "request_id": "team_skill_evolve_stages",
            "content": "done",
        },
    )
    rail = _FakeRail([[detecting_event], [generating_event], [outcome_event]], pending_first=False)

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )
    monkeypatch.setattr(team_helpers, "parse_stream_chunk", lambda evt: None)

    task = asyncio.create_task(
        _TeamHelpersTestApi.watch_team_evolution_and_push("web", "sess-stages", rail)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    status_pushes = [
        push for push in _FakeTransport.pushes
        if push["payload"]["event_type"] == "chat.evolution_status"
    ]
    assert [push["payload"]["status"] for push in status_pushes] == [
        "start",
        "end",
    ]
    assert [push["payload"]["stage"] for push in status_pushes] == [
        "generating",
        "completed",
    ]
    assert {push["request_id"] for push in status_pushes} == {"team_skill_evolve_stages"}


@pytest.mark.anyio
async def test_team_evolution_monitor_uses_meta_request_id_and_ends_on_cancelled(monkeypatch):
    _FakeTransport.pushes = []
    detecting_event = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {
                "event_kind": "progress",
                "stage": "detecting_signals",
                "request_id": "team_skill_evolve_meta",
            },
            "content": "[Team Skill Evolution] detecting",
        },
    )
    generating_event = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {
                "event_kind": "progress",
                "stage": "generating_updates",
                "request_id": "team_skill_evolve_meta",
            },
            "content": "[Team Skill Evolution] generating",
        },
    )
    cancelled_event = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {
                "event_kind": "progress",
                "stage": "cancelled",
                "request_id": "team_skill_evolve_meta",
            },
            "content": "no actionable evolution signals detected",
        },
    )
    rail = _FakeRail(
        [[detecting_event], [generating_event], [cancelled_event]],
        pending_first=False,
    )

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )
    monkeypatch.setattr(team_helpers, "parse_stream_chunk", lambda evt: None)

    task = asyncio.create_task(
        _TeamHelpersTestApi.watch_team_evolution_and_push("web", "sess-meta", rail)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    status_pushes = [
        push for push in _FakeTransport.pushes
        if push["payload"]["event_type"] == "chat.evolution_status"
    ]
    assert [push["payload"]["status"] for push in status_pushes] == [
        "start",
        "end",
    ]
    assert [push["payload"]["stage"] for push in status_pushes] == [
        "generating",
        "hidden",
    ]
    assert {push["request_id"] for push in status_pushes} == {"team_skill_evolve_meta"}


@pytest.mark.anyio
async def test_team_evolution_monitor_filters_progress_by_request_id(monkeypatch):
    _FakeTransport.pushes = []
    request_a_detecting = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {
                "event_kind": "progress",
                "stage": "detecting_signals",
                "request_id": "team_skill_evolve_a",
            },
            "content": "[Team Skill Evolution] detecting A",
        },
    )
    request_b_generating = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {
                "event_kind": "progress",
                "stage": "generating_updates",
                "request_id": "team_skill_evolve_b",
            },
            "content": "[Team Skill Evolution] generating B",
        },
    )
    request_a_generating = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {
                "event_kind": "progress",
                "stage": "generating_updates",
                "request_id": "team_skill_evolve_a",
            },
            "content": "[Team Skill Evolution] generating A",
        },
    )
    request_a_completed = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {
                "event_kind": "progress",
                "stage": "completed",
                "request_id": "team_skill_evolve_a",
            },
            "content": "done A",
        },
    )
    rail = _FakeRail(
        [[request_a_detecting], [request_a_generating, request_b_generating], [request_a_completed]],
        pending_first=False,
    )

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )
    monkeypatch.setattr(team_helpers, "parse_stream_chunk", lambda evt: None)

    task = asyncio.create_task(
        _TeamHelpersTestApi.watch_team_evolution_and_push("web", "sess-filter", rail)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    status_pushes = [
        push for push in _FakeTransport.pushes
        if push["payload"]["event_type"] == "chat.evolution_status"
    ]
    assert [(push["request_id"], push["payload"]["status"], push["payload"]["stage"]) for push in status_pushes] == [
        ("team_skill_evolve_a", "start", "generating"),
        ("team_skill_evolve_a", "end", "completed"),
    ]
    assert all("generating B" not in push["payload"].get("message", "") for push in status_pushes)


@pytest.mark.anyio
async def test_team_evolution_monitor_uses_delivery_context_metadata(monkeypatch):
    _FakeTransport.pushes = []
    approval_event = SimpleNamespace(
        type="chat.ask_user_question",
        payload={"request_id": "team_skill_evolve_meta", "questions": [{"header": "x"}]},
    )
    rail = _FakeRail([[approval_event]], pending_first=False)
    recorded_calls: list[dict] = []

    def _fake_build_server_push_message(**kwargs):
        recorded_calls.append(dict(kwargs))
        message = dict(kwargs)
        message["channel_id"] = kwargs["fallback_channel_id"]
        message["metadata"] = {"route": "from-delivery-context"}
        return message

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )
    monkeypatch.setattr(
        team_helpers,
        "build_server_push_message",
        _fake_build_server_push_message,
    )
    monkeypatch.setattr(team_helpers, "parse_stream_chunk", lambda evt: None)

    task = asyncio.create_task(
        _TeamHelpersTestApi.watch_team_evolution_and_push("web", "sess-meta", rail)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert recorded_calls
    assert all(call["session_id"] == "sess-meta" for call in recorded_calls)
    assert all(call["fallback_channel_id"] == "web" for call in recorded_calls)
    assert _FakeTransport.pushes
    assert all(push["metadata"] == {"route": "from-delivery-context"} for push in _FakeTransport.pushes)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "expected_stage"),
    [
        ("completed", "completed"),
        ("failed", "hidden"),
        ("timed_out", "hidden"),
    ],
)
async def test_team_evolution_monitor_reads_terminal_outcome_from_host_events(
    monkeypatch,
    status: str,
    expected_stage: str,
):
    _FakeTransport.pushes = []
    outcome_event = SimpleNamespace(
        type="chat.evolution_status",
        payload={
            "_evolution_meta": {"event_kind": "outcome", "status": status},
            "request_id": "team_skill_evolve_outcome",
            "content": status,
        },
    )
    rail = _FakeRail([[outcome_event]], pending_first=True)

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )
    monkeypatch.setattr(team_helpers, "parse_stream_chunk", lambda evt: None)

    task = asyncio.create_task(
        _TeamHelpersTestApi.watch_team_evolution_and_push("web", "sess-outcome", rail)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    status_pushes = [
        push for push in _FakeTransport.pushes
        if push["payload"]["event_type"] == "chat.evolution_status"
    ]
    assert [push["payload"]["status"] for push in status_pushes] == ["start", "end"]
    assert {push["request_id"] for push in status_pushes} == {"team_skill_evolve_outcome"}
    assert status_pushes[-1]["payload"]["stage"] == expected_stage
    assert status_pushes[-1]["payload"]["message"] == status


@pytest.mark.anyio
async def test_team_evolution_monitor_maps_noop_progress_to_no_evolution_generated(
    monkeypatch,
):
    _FakeTransport.pushes = []
    progress_event = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {"event_kind": "progress", "stage": "completed"},
            "content": "No evolution signals detected",
        },
    )
    rail = _FakeRail([[progress_event]], pending_first=False)

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )
    monkeypatch.setattr(team_helpers, "parse_stream_chunk", lambda evt: None)

    task = asyncio.create_task(
        _TeamHelpersTestApi.watch_team_evolution_and_push("web", "sess-noop", rail)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    status_pushes = [
        push for push in _FakeTransport.pushes
        if push["payload"]["event_type"] == "chat.evolution_status"
    ]
    assert [push["payload"]["status"] for push in status_pushes] == ["start", "end"]
    assert status_pushes[-1]["payload"]["stage"] == "no_evolution_no_signal"


@pytest.mark.anyio
async def test_team_evolution_monitor_uses_approval_request_id_without_provisional_start(monkeypatch):
    _FakeTransport.pushes = []
    approval_event = SimpleNamespace(
        type="chat.ask_user_question",
        payload={"request_id": "team_skill_evolve_real", "questions": [{"header": "x"}]},
    )

    class _PendingThenApprovalRail:
        def __init__(self):
            self._drain_calls = 0

        async def drain_pending_approval_events(self, wait: bool = False, timeout: float | None = None):
            assert wait is False
            self._drain_calls += 1
            if self._drain_calls == 1:
                return [approval_event]
            return []

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )
    monkeypatch.setattr(team_helpers, "parse_stream_chunk", lambda evt: None)

    task = asyncio.create_task(
        _TeamHelpersTestApi.watch_team_evolution_and_push(
            "web",
            "sess-rebind",
            _PendingThenApprovalRail(),
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    status_starts = []
    approval_pushes = []
    for push in _FakeTransport.pushes:
        event_type = push["payload"]["event_type"]
        if event_type == "chat.ask_user_question":
            approval_pushes.append(push)
        if (
            event_type == "chat.evolution_status"
            and push["payload"]["status"] == "start"
        ):
            status_starts.append(push)
    assert len(status_starts) == 1
    assert status_starts[0]["request_id"] == "team_skill_evolve_real"
    assert approval_pushes[0]["request_id"] == "team_skill_evolve_real"
    assert approval_pushes[0]["payload"]["request_id"] == "team_skill_evolve_real"


@pytest.mark.anyio
async def test_team_evolution_monitor_keeps_idle_listener_after_timeout(monkeypatch):
    _FakeTransport.pushes = []
    rail = _FakeRail([], pending_first=True)

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )
    monkeypatch.setattr(team_helpers, "TEAM_EVOLUTION_IDLE_SLEEP_SEC", 0.001)
    monkeypatch.setattr(team_helpers, "TEAM_EVOLUTION_EVENT_TIMEOUT_SEC", 0.01)

    task = asyncio.create_task(
        _TeamHelpersTestApi.watch_team_evolution_and_push(
            "web",
            "sess-idle",
            rail,
        )
    )
    await asyncio.sleep(0.03)

    assert task.done() is False
    assert _FakeTransport.pushes == []
    assert rail.drain_waits
    assert set(rail.drain_waits) == {False}

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_team_evolution_monitor_times_out_after_idle_progress(monkeypatch):
    _FakeTransport.pushes = []
    rail = _FakeProgressOnlyRail()

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )
    monkeypatch.setattr(team_helpers, "TEAM_EVOLUTION_IDLE_SLEEP_SEC", 0.001)
    monkeypatch.setattr(team_helpers, "TEAM_EVOLUTION_EVENT_TIMEOUT_SEC", 0.01)

    await _TeamHelpersTestApi.watch_team_evolution_and_push("web", "sess-timeout", rail)

    status_pushes = [
        push for push in _FakeTransport.pushes
        if push["payload"]["event_type"] == "chat.evolution_status"
    ]
    assert [push["payload"]["status"] for push in status_pushes] == ["start", "end"]
    assert status_pushes[0]["request_id"] == "team_skill_evolve_timeout"
    assert status_pushes[-1]["payload"]["stage"] == "hidden"
    assert "timed out" in status_pushes[-1]["payload"]["message"]
    assert rail.cleanup_calls == 1


@pytest.mark.anyio
async def test_ensure_team_evolution_watcher_starts_without_reasoning_gate(monkeypatch):
    registered: dict[str, asyncio.Task] = {}

    class _FakeManager:
        @staticmethod
        def get_team_evolution_watcher(session_id: str):
            return None

        @staticmethod
        def get_team_skill_rail(session_id: str):
            return object()

        @staticmethod
        def register_team_evolution_watcher(
            session_id: str,
            task: asyncio.Task,
        ) -> None:
            registered[session_id] = task

        @staticmethod
        def pop_team_evolution_watcher(session_id: str):
            return registered.pop(session_id, None)

    async def _fake_watch(channel_id, session_id, rail):
        await asyncio.sleep(3600)

    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id: _FakeManager())
    monkeypatch.setattr(team_helpers, "_watch_team_evolution_and_push", _fake_watch)

    _TeamHelpersTestApi.ensure_team_evolution_watcher("web", "sess-2")

    watcher = registered["sess-2"]
    assert isinstance(watcher, asyncio.Task)
    watcher.cancel()
    with pytest.raises(asyncio.CancelledError):
        await watcher


@pytest.mark.anyio
async def test_ensure_team_evolution_watcher_skips_disabled_auto_scan(monkeypatch):
    registered: dict[str, asyncio.Task] = {}

    class _DisabledRail:
        auto_scan = False

    class _FakeManager:
        @staticmethod
        def get_team_evolution_watcher(session_id: str):
            return None

        @staticmethod
        def get_team_skill_rail(session_id: str):
            return _DisabledRail()

        @staticmethod
        def register_team_evolution_watcher(
            session_id: str,
            task: asyncio.Task,
        ) -> None:
            registered[session_id] = task

    async def _fake_watch(channel_id, session_id, rail):
        raise AssertionError("disabled team evolution watcher should not start")

    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id: _FakeManager())
    monkeypatch.setattr(team_helpers, "_watch_team_evolution_and_push", _fake_watch)

    _TeamHelpersTestApi.ensure_team_evolution_watcher("web", "sess-disabled")

    assert registered == {}


@pytest.mark.anyio
async def test_consume_stream_with_query_launches_watcher_after_runtime_ready(monkeypatch):
    calls: list[str] = []

    class _FakeManager:
        @staticmethod
        def commit_runtime_ready(session_id: str, team_name: str) -> None:
            calls.append(f"commit:{session_id}:{team_name}")

        @staticmethod
        async def attach_distributed_hooks_for_runner_runtime(**kwargs) -> None:
            calls.append(
                f"hooks:{kwargs['session_id']}:{kwargs['team_name']}:{kwargs['channel_id']}"
            )

        @staticmethod
        def clear_pending_runtime(session_id: str) -> None:
            calls.append(f"clear:{session_id}")

        @staticmethod
        def pop_stream_task(session_id: str):
            calls.append(f"pop:{session_id}")
            return None


    async def _fake_stream(**kwargs):
        yield SimpleNamespace(kind="ready")

    monkeypatch.setattr(
        team_helpers.Runner,
        "run_agent_team_streaming",
        _fake_stream,
    )
    monkeypatch.setattr(
        team_helpers,
        "parse_stream_chunk",
        lambda chunk: {
            "event_type": "team.runtime_ready",
            "team_name": "ready-team",
            "activation_kind": "resume",
        },
    )
    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id: _FakeManager())
    monkeypatch.setattr(
        team_helpers,
        "sync_team_identity_metadata",
        lambda **kwargs: calls.append(f"sync:{kwargs['session_id']}:{kwargs['ready_team_name']}"),
    )

    async def _fake_monitor(
        channel_id: str | None,
        session_id: str,
        team_name: str,
        hide_dm: bool = False,
    ) -> None:
        calls.append(f"monitor:{session_id}:{team_name}")

    monkeypatch.setattr(team_helpers, "ensure_monitor_for_active_runtime", _fake_monitor)
    monkeypatch.setattr(
        team_helpers,
        "ensure_team_evolution_watcher",
        lambda channel_id, session_id, *, source="unknown": calls.append(
            f"watcher:{session_id}:{source}"
        ),
    )

    await _TeamHelpersTestApi.consume_stream_with_query(
        "web",
        "sess-runtime",
        SimpleNamespace(team_name="spec-team"),
        "hello",
    )

    assert calls[:5] == [
        "sync:sess-runtime:ready-team",
        "commit:sess-runtime:ready-team",
        "hooks:sess-runtime:ready-team:web",
        "monitor:sess-runtime:ready-team",
        "watcher:sess-runtime:runtime_ready",
    ]


@pytest.mark.anyio
async def test_consume_monitor_events_only_broadcasts_monitor_events(monkeypatch):
    broadcasted: list[dict[str, object]] = []
    event = {"event_type": "team.task", "event": {"type": "team.task.completed", "task_id": "task-1"}}

    class _FakeMonitorEventHandler:
        def __init__(self, events: list[dict[str, object]]):
            self._events = list(events)

        async def events(self):
            for item in self._events:
                yield item

    monkeypatch.setattr(team_helpers, "_broadcast_event", lambda *args: broadcasted.append(args[2]))

    monitor_handler = _FakeMonitorEventHandler([event])
    await _TeamHelpersTestApi.consume_monitor_events("web", "sess-monitor", monitor_handler)

    assert broadcasted == [event]


@pytest.mark.anyio
async def test_handle_team_evolve_list_command_returns_team_store_summary(monkeypatch):
    record = SimpleNamespace(
        score=0.88,
        usage_stats=SimpleNamespace(
            times_used=2,
            times_presented=3,
            times_positive=1,
            times_negative=0,
        ),
        change=SimpleNamespace(section="workflow", content="Improve retry flow\nSecond line"),
    )

    class _FakeStore:
        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def list_skill_names() -> list[str]:
            return ["demo-skill"]

        @staticmethod
        async def get_records_by_score(skill_name: str):
            assert skill_name == "demo-skill"
            return [record]

    class _FakeManager:
        @staticmethod
        def get_team_skill_rail(session_id: str):
            assert session_id == "sess-team-list"
            return SimpleNamespace(store=_FakeStore())

    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id: _FakeManager())

    result = await _TeamHelpersTestApi.handle_team_evolve_list_command(
        "web",
        "sess-team-list",
        "/evolve_list demo-skill",
    )

    assert result is not None
    assert result["result_type"] == "answer"
    assert 'Skill "demo-skill"' in result["output"]
    assert "Improve retry flow" in result["output"]


@pytest.mark.anyio
async def test_process_team_message_stream_handles_team_evolve_list(monkeypatch):
    record = SimpleNamespace(
        score=1.0,
        usage_stats=None,
        change=SimpleNamespace(section="workflow", content="First summary line"),
    )

    class _FakeStore:
        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def list_skill_names() -> list[str]:
            return ["demo-skill"]

        @staticmethod
        async def get_records_by_score(skill_name: str):
            return [record]

    class _FakeManager:
        @staticmethod
        async def get_or_create_team(**kwargs):
            return object()

        @staticmethod
        def has_stream_task(session_id: str) -> bool:
            return False

        @staticmethod
        def get_team_skill_rail(session_id: str):
            return SimpleNamespace(store=_FakeStore())

    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id: _FakeManager())

    request = SimpleNamespace(
        session_id="sess-team-stream",
        request_id="req-team-stream",
        channel_id="web",
        metadata=None,
    )
    inputs = {"query": "/evolve_list demo-skill"}

    chunks = []
    async for chunk in team_helpers.process_team_message_stream(
        request,
        inputs,
        object(),
    ):
        chunks.append(chunk)

    assert len(chunks) == 3
    assert chunks[0].payload is not None
    assert chunks[0].payload["event_type"] == "chat.final"
    assert 'Skill "demo-skill"' in chunks[0].payload["content"]
    assert chunks[1].payload == {
        "event_type": "chat.processing_status",
        "session_id": "sess-team-stream",
        "is_processing": False,
        "is_complete": True,
    }
    assert chunks[1].is_complete is False
    assert chunks[2].is_complete is True


@pytest.mark.anyio
async def test_process_team_message_stream_emits_processing_done_for_evolve_approval(monkeypatch):
    approval_event = SimpleNamespace(
        payload={"request_id": "team_skill_evolve_req1", "questions": [{"header": "x"}]},
    )

    class _FakeStore:
        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def list_skill_names() -> list[str]:
            return ["demo-skill"]

    class _FakeRail:
        store = _FakeStore()

        @staticmethod
        async def request_user_evolution(skill_name: str, user_query: str):
            return SimpleNamespace(approval_event=approval_event, records=[object()])

    class _FakeManager:
        @staticmethod
        async def get_or_create_team(**kwargs):
            return object()

        @staticmethod
        def has_stream_task(session_id: str) -> bool:
            return True

        @staticmethod
        def get_team_skill_rail(session_id: str):
            return _FakeRail()

    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id: _FakeManager())
    monkeypatch.setattr(team_helpers, "ensure_team_evolution_watcher", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        team_helpers,
        "parse_stream_chunk",
        lambda evt: {
            "event_type": "chat.ask_user_question",
            "request_id": evt.payload["request_id"],
            "questions": evt.payload["questions"],
        },
    )

    request = SimpleNamespace(
        session_id="sess-team-evolve",
        request_id="req-team-evolve",
        channel_id="web",
        metadata=None,
    )
    inputs = {"query": "/evolve demo-skill improve review flow"}

    chunks = []
    async for chunk in team_helpers.process_team_message_stream(
        request,
        inputs,
        object(),
    ):
        chunks.append(chunk)

    assert [chunk.payload for chunk in chunks] == [
        {
            "event_type": "chat.ask_user_question",
            "request_id": "team_skill_evolve_req1",
            "questions": [{"header": "x"}],
        },
        {
            "event_type": "chat.processing_status",
            "session_id": "sess-team-evolve",
            "is_processing": False,
            "is_complete": True,
        },
        {"event_type": "chat.done"},
    ]
    assert [chunk.is_complete for chunk in chunks] == [False, False, True]


@pytest.mark.anyio
async def test_process_team_message_stream_does_not_emit_evolution_status_for_no_evolve_records(monkeypatch):
    class _FakeStore:
        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def list_skill_names() -> list[str]:
            return ["demo-skill"]

    class _FakeRail:
        store = _FakeStore()

        @staticmethod
        async def request_user_evolution(skill_name: str, user_query: str):
            return SimpleNamespace(approval_event=None, records=[])

    class _FakeManager:
        @staticmethod
        async def get_or_create_team(**kwargs):
            return object()

        @staticmethod
        def has_stream_task(session_id: str) -> bool:
            return True

        @staticmethod
        def get_team_skill_rail(session_id: str):
            return _FakeRail()

    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id: _FakeManager())
    monkeypatch.setattr(team_helpers, "ensure_team_evolution_watcher", lambda *args, **kwargs: None)

    request = SimpleNamespace(
        session_id="sess-team-evolve-noop",
        request_id="req-team-evolve-noop",
        channel_id="web",
        metadata=None,
    )
    inputs = {"query": "/evolve demo-skill improve review flow"}

    chunks = []
    async for chunk in team_helpers.process_team_message_stream(
        request,
        inputs,
        object(),
    ):
        chunks.append(chunk)

    assert [chunk.payload["event_type"] for chunk in chunks if chunk.payload] == [
        "chat.final",
        "chat.processing_status",
    ]
    assert chunks[-1].is_complete is True


@pytest.mark.anyio
async def test_consume_stream_with_query_broadcasts_leader_and_teammate_outputs(monkeypatch):
    broadcasted: list[dict] = []
    ready_calls: list[tuple[str, str]] = []

    async def _fake_stream(**kwargs):
        yield SimpleNamespace(
            type="team.runtime_ready",
            payload={
                "event_type": "team.runtime_ready",
                "team_name": "demo-team",
                "activation_kind": "create",
            },
            role=TeamRole.LEADER,
        )
        yield SimpleNamespace(
            type="answer",
            payload={"output": {"output": "leader answer"}, "result_type": "answer"},
            role=TeamRole.LEADER,
        )
        yield SimpleNamespace(
            type="answer",
            payload={"output": {"output": "teammate answer"}, "result_type": "answer"},
            role=TeamRole.TEAMMATE,
            source_member="analyst",
        )
        yield SimpleNamespace(
            type="answer",
            payload={"output": {"output": "human answer"}, "result_type": "answer"},
            role=SimpleNamespace(value=TeamRole.HUMAN_AGENT.value),
        )

    class _FakeRunner:
        run_agent_team_streaming = staticmethod(_fake_stream)

        @staticmethod
        async def get_agent_team_monitor(team_name: str, session_id: str, hide_dm: bool = False):
            return None

    class _FakeManager:
        @staticmethod
        def commit_runtime_ready(session_id: str, team_name: str) -> None:
            ready_calls.append((session_id, team_name))

        @staticmethod
        def clear_pending_runtime(session_id: str) -> None:
            pass

        @staticmethod
        def pop_stream_task(session_id: str) -> None:
            pass

        @staticmethod
        def get_monitor(session_id: str):
            return None

        @staticmethod
        async def attach_distributed_hooks_for_runner_runtime(
            team_name: str,
            session_id: str,
            channel_id: str,
        ) -> None:
            pass

    monkeypatch.setattr(team_helpers, "Runner", _FakeRunner)
    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id: _FakeManager())
    monkeypatch.setattr(
        team_helpers,
        "_broadcast_event",
        lambda channel_id, session_id, event: broadcasted.append(event),
    )
    monkeypatch.setattr(team_helpers, "ensure_team_evolution_watcher", lambda *args, **kwargs: None)
    monkeypatch.setattr(team_helpers, "get_session_metadata", lambda session_id: {})
    monkeypatch.setattr(team_helpers, "update_session_metadata", lambda **kwargs: None)

    await _TeamHelpersTestApi.consume_stream_with_query(
        "web",
        "sess-leader-only",
        SimpleNamespace(team_name="demo-team"),
        "hello",
    )

    assert ready_calls == [("sess-leader-only", "demo-team")]
    assert [event["event_type"] for event in broadcasted] == [
        "team.runtime_ready",
        "chat.final",
        "chat.final",
    ]
    assert broadcasted[1]["content"] == "leader answer"
    # Teammate event includes role and member_name
    assert broadcasted[2]["content"] == "teammate answer"
    assert broadcasted[2]["role"] == TeamRole.TEAMMATE.value
    assert broadcasted[2]["member_name"] == "analyst"


def test_extract_hide_dm_directive_strips_prefix_and_flags():
    cleaned, hide_dm = team_helpers._extract_hide_dm_directive(  # pylint: disable=protected-access
        "/hide_dm please summarize"
    )
    assert hide_dm is True
    assert cleaned == "please summarize"


def test_extract_hide_dm_directive_ignores_non_prefix():
    cleaned, hide_dm = team_helpers._extract_hide_dm_directive(  # pylint: disable=protected-access
        "/hide_dmsomething else"
    )
    assert hide_dm is False
    assert cleaned == "/hide_dmsomething else"


def test_extract_hide_dm_directive_handles_bare_directive():
    cleaned, hide_dm = team_helpers._extract_hide_dm_directive("/hide_dm")  # pylint: disable=protected-access
    assert hide_dm is True
    assert cleaned == ""


@pytest.mark.anyio
async def test_consume_stream_with_query_propagates_hide_dm_to_monitor(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_stream(**kwargs):
        yield SimpleNamespace(
            type="team.runtime_ready",
            payload={
                "event_type": "team.runtime_ready",
                "team_name": "demo-team",
                "activation_kind": "create",
            },
            role=TeamRole.LEADER,
        )

    class _FakeRunner:
        run_agent_team_streaming = staticmethod(_fake_stream)

        @staticmethod
        async def get_agent_team_monitor(team_name: str, session_id: str, hide_dm: bool = False):
            captured["team_name"] = team_name
            captured["session_id"] = session_id
            captured["hide_dm"] = hide_dm
            return None

    class _FakeManager:
        @staticmethod
        def commit_runtime_ready(session_id: str, team_name: str) -> None:
            pass

        @staticmethod
        def clear_pending_runtime(session_id: str) -> None:
            pass

        @staticmethod
        def pop_stream_task(session_id: str) -> None:
            pass

        @staticmethod
        def get_monitor(session_id: str):
            return None

        @staticmethod
        async def attach_distributed_hooks_for_runner_runtime(
            team_name: str,
            session_id: str,
            channel_id: str,
        ) -> None:
            pass

    monkeypatch.setattr(team_helpers, "Runner", _FakeRunner)
    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id: _FakeManager())
    monkeypatch.setattr(team_helpers, "_broadcast_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(team_helpers, "get_session_metadata", lambda session_id: {})
    monkeypatch.setattr(team_helpers, "update_session_metadata", lambda **kwargs: None)

    await team_helpers._consume_stream_with_query(  # pylint: disable=protected-access
        "web",
        "sess-hide-dm",
        SimpleNamespace(team_name="demo-team"),
        "hello",
        hide_dm=True,
    )

    assert captured == {
        "team_name": "demo-team",
        "session_id": "sess-hide-dm",
        "hide_dm": True,
    }


@pytest.mark.anyio
async def test_handle_team_slash_command_requires_explicit_evolve_intent(monkeypatch):
    class _FakeStore:
        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def list_skill_names() -> list[str]:
            return ["demo-skill"]

    rail = SimpleNamespace(
        store=_FakeStore(),
        request_user_evolution=None,
    )

    class _FakeManager:
        @staticmethod
        def get_team_skill_rail(session_id: str):
            return rail

    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id: _FakeManager())

    result = await _TeamHelpersTestApi.handle_team_slash_command(
        "web",
        "sess-team-evolve",
        "/evolve demo-skill",
    )

    assert result == {
        "output": "请补充演进意图：`/evolve <skill_name> <user_query>`",
        "result_type": "error",
    }


@pytest.mark.anyio
async def test_handle_team_slash_command_submits_explicit_evolve_request(monkeypatch):
    recorded_calls: list[tuple[str, str]] = []
    watcher_calls: list[tuple[str | None, str, str]] = []

    class _FakeStore:
        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def list_skill_names() -> list[str]:
            return ["demo-skill"]

    class _FakeRail:
        store = _FakeStore()

        @staticmethod
        async def request_user_evolution(skill_name: str, user_query: str):
            recorded_calls.append((skill_name, user_query))
            return SimpleNamespace(
                approval_event=SimpleNamespace(
                    type="chat.ask_user_question",
                    payload={
                        "request_id": "team_skill_evolve_req1",
                        "questions": [{"header": "x"}],
                    },
                ),
                records=[object()],
            )

    class _FakeManager:
        @staticmethod
        def get_team_skill_rail(session_id: str):
            return _FakeRail()

    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id: _FakeManager())
    monkeypatch.setattr(
        team_helpers,
        "parse_stream_chunk",
        lambda evt: {
            "event_type": "chat.ask_user_question",
            "request_id": evt.payload["request_id"],
            "questions": evt.payload["questions"],
        },
    )
    monkeypatch.setattr(
        team_helpers,
        "ensure_team_evolution_watcher",
        lambda channel_id, session_id, *, source="unknown": watcher_calls.append(
            (channel_id, session_id, source)
        ),
    )

    result = await _TeamHelpersTestApi.handle_team_slash_command(
        "web",
        "sess-team-evolve",
        "/evolve demo-skill improve review flow",
    )

    assert recorded_calls == [("demo-skill", "improve review flow")]
    assert watcher_calls == []
    assert result == {
        "output": "Skill 'demo-skill' 演进请求已生成，请在审批弹框中确认。",
        "result_type": "answer",
        "approval_chunks": [
            {
                "event_type": "chat.ask_user_question",
                "request_id": "team_skill_evolve_req1",
                "questions": [{"header": "x"}],
            }
        ],
        "question_count": 1,
    }


@pytest.mark.anyio
async def test_handle_team_slash_command_simplify_reports_noop(monkeypatch):
    recorded_calls: list[tuple[str, str | None]] = []
    watcher_calls: list[tuple[str | None, str, str]] = []

    class _FakeStore:
        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def list_skill_names() -> list[str]:
            return ["demo-skill"]

    class _FakeRail:
        store = _FakeStore()

        @staticmethod
        async def request_simplify(skill_name: str, user_intent: str | None):
            recorded_calls.append((skill_name, user_intent))
            return None

    class _FakeManager:
        @staticmethod
        def get_team_skill_rail(session_id: str):
            return _FakeRail()

    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id: _FakeManager())
    monkeypatch.setattr(
        team_helpers,
        "ensure_team_evolution_watcher",
        lambda channel_id, session_id, *, source="unknown": watcher_calls.append(
            (channel_id, session_id, source)
        ),
    )

    result = await _TeamHelpersTestApi.handle_team_slash_command(
        "web",
        "sess-team-simplify",
        "/evolve_simplify demo-skill",
    )

    assert recorded_calls == [("demo-skill", None)]
    assert watcher_calls == []
    assert result == {
        "output": "Skill 'demo-skill' 经验库状态良好，无需整理。",
        "result_type": "answer",
    }
