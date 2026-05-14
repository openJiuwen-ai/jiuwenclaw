# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team evolution monitor helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from openjiuwen.agent_teams.schema.team import TeamRole

from jiuwenclaw.server.runtime.agent_adapter import team_helpers


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

    def has_pending_evolution_tasks(self) -> bool:
        return self._pending_first and self._drain_calls == 0

    async def drain_pending_approval_events(self, wait: bool = False, timeout: float | None = None):
        self._drain_calls += 1
        if self._batches:
            return self._batches.pop(0)
        return []

    @staticmethod
    def drain_evolution_outcomes():
        return []


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
    ) -> None:
        ensure_watcher = getattr(team_helpers, "_ensure_team_evolution_watcher")
        ensure_watcher(channel_id, session_id)

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
        "jiuwenclaw.server.gateway_push.WebSocketGatewayPushTransport",
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
        "jiuwenclaw.server.gateway_push.WebSocketGatewayPushTransport",
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
async def test_team_evolution_monitor_rebinds_to_real_request_id_after_provisional_start(monkeypatch):
    _FakeTransport.pushes = []
    approval_event = SimpleNamespace(
        type="chat.ask_user_question",
        payload={"request_id": "team_skill_evolve_real", "questions": [{"header": "x"}]},
    )

    class _PendingThenApprovalRail:
        def __init__(self):
            self._drain_calls = 0

        def has_pending_evolution_tasks(self) -> bool:
            return self._drain_calls == 0

        async def drain_pending_approval_events(self, wait: bool = False, timeout: float | None = None):
            self._drain_calls += 1
            if self._drain_calls == 1:
                return [approval_event]
            return []

        @staticmethod
        def drain_evolution_outcomes():
            return []

    monkeypatch.setattr(
        "jiuwenclaw.server.gateway_push.WebSocketGatewayPushTransport",
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
    assert status_starts[0]["request_id"].startswith("team_evolve_sess-rebind_")
    assert status_starts[1]["request_id"] == "team_skill_evolve_real"
    assert approval_pushes[0]["request_id"] == "team_skill_evolve_real"
    assert approval_pushes[0]["payload"]["request_id"] == "team_skill_evolve_real"


@pytest.mark.anyio
async def test_team_evolution_monitor_pushes_start_before_drain_finishes(monkeypatch):
    _FakeTransport.pushes = []
    release = asyncio.Event()

    class _BlockingRail:
        @staticmethod
        def has_pending_evolution_tasks() -> bool:
            return True

        async def drain_pending_approval_events(self, wait: bool = False, timeout: float | None = None):
            await release.wait()
            return []

        @staticmethod
        def drain_evolution_outcomes():
            return []

    monkeypatch.setattr(
        "jiuwenclaw.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )

    task = asyncio.create_task(
        _TeamHelpersTestApi.watch_team_evolution_and_push(
            "web",
            "sess-start",
            _BlockingRail(),
        )
    )
    await asyncio.sleep(0.05)

    assert _FakeTransport.pushes
    assert _FakeTransport.pushes[0]["payload"]["event_type"] == "chat.evolution_status"
    assert _FakeTransport.pushes[0]["payload"]["status"] == "start"

    release.set()
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_team_evolution_monitor_does_not_end_when_wait_timeout_returns_empty(monkeypatch):
    _FakeTransport.pushes = []

    class _StillPendingRail:
        @staticmethod
        def has_pending_evolution_tasks() -> bool:
            return True

        async def drain_pending_approval_events(self, wait: bool = False, timeout: float | None = None):
            return []

        @staticmethod
        def drain_evolution_outcomes():
            return []

    monkeypatch.setattr(
        "jiuwenclaw.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )

    task = asyncio.create_task(
        _TeamHelpersTestApi.watch_team_evolution_and_push(
            "web",
            "sess-timeout",
            _StillPendingRail(),
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    event_types = [push["payload"]["event_type"] for push in _FakeTransport.pushes]
    statuses = []
    for push in _FakeTransport.pushes:
        if push["payload"]["event_type"] == "chat.evolution_status":
            statuses.append(push["payload"]["status"])
    assert event_types == ["chat.evolution_status"]
    assert statuses == ["start"]


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

    assert len(chunks) == 2
    assert chunks[0].payload is not None
    assert chunks[0].payload["event_type"] == "chat.final"
    assert 'Skill "demo-skill"' in chunks[0].payload["content"]
    assert chunks[1].is_complete is True


@pytest.mark.anyio
async def test_consume_stream_with_query_broadcasts_only_leader_team_outputs(monkeypatch):
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
        )
        yield SimpleNamespace(
            type="answer",
            payload={"output": {"output": "human answer"}, "result_type": "answer"},
            role=SimpleNamespace(value=TeamRole.HUMAN_AGENT.value),
        )

    class _FakeRunner:
        run_agent_team_streaming = staticmethod(_fake_stream)

        @staticmethod
        async def get_agent_team_monitor(team_name: str, session_id: str):
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
    monkeypatch.setattr(team_helpers, "get_session_metadata", lambda session_id: {})
    monkeypatch.setattr(team_helpers, "update_session_metadata", lambda **kwargs: None)

    await team_helpers._consume_stream_with_query(  # pylint: disable=protected-access
        "web",
        "sess-leader-only",
        SimpleNamespace(team_name="demo-team"),
        "hello",
    )

    assert ready_calls == [("sess-leader-only", "demo-team")]
    assert [event["event_type"] for event in broadcasted] == [
        "team.runtime_ready",
        "chat.final",
    ]
    assert broadcasted[1]["content"] == "leader answer"


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
    watcher_calls: list[tuple[str | None, str]] = []

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
            return "team_skill_evolve_req1"

    class _FakeManager:
        @staticmethod
        def get_team_skill_rail(session_id: str):
            return _FakeRail()

    monkeypatch.setattr(team_helpers, "get_team_manager", lambda channel_id: _FakeManager())
    monkeypatch.setattr(
        team_helpers,
        "_ensure_team_evolution_watcher",
        lambda channel_id, session_id: watcher_calls.append((channel_id, session_id)),
    )

    result = await _TeamHelpersTestApi.handle_team_slash_command(
        "web",
        "sess-team-evolve",
        "/evolve demo-skill improve review flow",
    )

    assert recorded_calls == [("demo-skill", "improve review flow")]
    assert watcher_calls == [("web", "sess-team-evolve")]
    assert result == {
        "output": "Skill 'demo-skill' 演进请求已提交，请等待审批。",
        "result_type": "answer",
    }


@pytest.mark.anyio
async def test_handle_team_slash_command_simplify_reports_noop(monkeypatch):
    recorded_calls: list[tuple[str, str | None]] = []

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

    result = await _TeamHelpersTestApi.handle_team_slash_command(
        "web",
        "sess-team-simplify",
        "/evolve_simplify demo-skill",
    )

    assert recorded_calls == [("demo-skill", None)]
    assert result == {
        "output": "Skill 'demo-skill' 经验库状态良好，无需整理。",
        "result_type": "answer",
    }
