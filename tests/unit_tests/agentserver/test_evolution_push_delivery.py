# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.server.runtime.agent_adapter import interface_deep as interface_deep_module
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenClawDeepAdapter


class _FakeTransport:
    pushes: list[dict] = []

    def __init__(self):
        self.pushes = self.__class__.pushes

    async def send_push(self, payload: dict) -> None:
        self.pushes.append(payload)


def _approval_event(request_id: str = "team_skill_evolve_req1") -> SimpleNamespace:
    return SimpleNamespace(
        type="chat.ask_user_question",
        payload={"request_id": request_id, "questions": [{"header": "x"}]},
    )


def _outcome_event(status: str, message: str) -> SimpleNamespace:
    return SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {"event_kind": "outcome", "status": status},
            "content": message,
        },
    )


def _progress_event(content: str, *, stage: str | None = None) -> SimpleNamespace:
    payload = {"content": content}
    if stage is not None:
        payload["_evolution_meta"] = {"event_kind": "progress", "stage": stage}
    return SimpleNamespace(type="llm_reasoning", payload=payload)


class _FakeEvolutionRail:
    def __init__(self, batches: list[list[object]] | None = None) -> None:
        self._batches = list(
            batches
            if batches is not None
            else [[_approval_event(), _outcome_event("completed", "done")]]
        )
        self.drain_waits: list[bool] = []
        self.cleanup_calls = 0
        self.auto_scan = True
        self.llm_updates: list[tuple[object, str | None]] = []

    def update_llm(self, model: object, model_name: str | None) -> None:
        self.llm_updates.append((model, model_name))

    async def drain_pending_approval_events(
        self,
        wait: bool = False,
        timeout: float | None = None,
    ):
        self.drain_waits.append(wait)
        if self._batches:
            return self._batches.pop(0)
        return []

    async def cleanup_background_tasks(self) -> None:
        self.cleanup_calls += 1


class _TestAdapter(JiuWenClawDeepAdapter):
    @classmethod
    def build_with_rail(cls, rail: _FakeEvolutionRail) -> "_TestAdapter":
        adapter = object.__new__(cls)
        setattr(adapter, "_skill_evolution_rail", rail)
        return adapter

    async def watch_evolution_and_push(
        self,
        request_id: str,
        channel_id: str,
        session_id: str,
    ) -> None:
        watcher = getattr(self, "_watch_evolution_and_push")
        await watcher(request_id, channel_id, session_id)


@pytest.mark.asyncio
async def test_normal_evolution_watcher_skips_status_when_auto_scan_disabled(monkeypatch):
    _FakeTransport.pushes = []
    rail = _FakeEvolutionRail()
    rail.auto_scan = False
    adapter = _TestAdapter.build_with_rail(rail)

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )

    await adapter.watch_evolution_and_push("stream-rid", "web", "sess-disabled")

    assert _FakeTransport.pushes == []
    assert rail.drain_waits == []
    assert rail.cleanup_calls == 0


@pytest.mark.asyncio
async def test_normal_evolution_watcher_uses_delivery_context_metadata(monkeypatch):
    _FakeTransport.pushes = []
    adapter = _TestAdapter.build_with_rail(_FakeEvolutionRail())

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
        interface_deep_module,
        "build_server_push_message",
        _fake_build_server_push_message,
    )

    await adapter.watch_evolution_and_push("stream-rid", "web", "sess-normal")

    assert recorded_calls
    assert all(call["session_id"] == "sess-normal" for call in recorded_calls)
    assert all(call["fallback_channel_id"] == "web" for call in recorded_calls)
    assert _FakeTransport.pushes
    assert all(
        push["metadata"] == {"route": "from-delivery-context"}
        for push in _FakeTransport.pushes
    )


@pytest.mark.asyncio
async def test_normal_evolution_watcher_reads_outcome_status_from_metadata(monkeypatch):
    _FakeTransport.pushes = []
    adapter = _TestAdapter.build_with_rail(
        _FakeEvolutionRail([[_outcome_event("failed", "failed")]])
    )

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )

    await adapter.watch_evolution_and_push("stream-rid", "web", "sess-normal-failed")

    status_pushes = [
        push for push in _FakeTransport.pushes
        if push["payload"]["event_type"] == "chat.evolution_status"
    ]
    assert [push["payload"]["status"] for push in status_pushes] == ["start", "end"]
    assert status_pushes[-1]["payload"]["stage"] == "hidden"


@pytest.mark.asyncio
async def test_normal_evolution_watcher_pushes_passive_progress_before_approval(monkeypatch):
    _FakeTransport.pushes = []
    rail = _FakeEvolutionRail(
        [
            [_progress_event("evolution progress")],
            [_approval_event("skill_evolve_progress_req")],
        ]
    )
    adapter = _TestAdapter.build_with_rail(rail)

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )

    await adapter.watch_evolution_and_push("stream-rid", "web", "sess-progress")

    event_types = [push["payload"]["event_type"] for push in _FakeTransport.pushes]
    assert event_types == [
        "chat.evolution_status",
        "chat.reasoning",
        "chat.ask_user_question",
        "chat.evolution_status",
    ]
    assert _FakeTransport.pushes[0]["payload"]["status"] == "start"
    assert _FakeTransport.pushes[1]["payload"]["content"] == "evolution progress"
    assert _FakeTransport.pushes[2]["payload"]["request_id"] == "skill_evolve_progress_req"
    assert _FakeTransport.pushes[3]["payload"]["status"] == "end"
    assert _FakeTransport.pushes[3]["payload"]["stage"] == "approval_required"
    assert rail.cleanup_calls == 1
    assert rail.drain_waits
    assert set(rail.drain_waits) == {False}


@pytest.mark.asyncio
async def test_normal_evolution_watcher_times_out_after_idle_progress(monkeypatch):
    _FakeTransport.pushes = []
    rail = _FakeEvolutionRail([[_progress_event("evolution progress")]])
    adapter = _TestAdapter.build_with_rail(rail)

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )
    monkeypatch.setattr(interface_deep_module, "TEAM_EVOLUTION_IDLE_SLEEP_SEC", 0.001)
    monkeypatch.setattr(interface_deep_module, "TEAM_EVOLUTION_EVENT_TIMEOUT_SEC", 0.01)

    await adapter.watch_evolution_and_push("stream-rid", "web", "sess-timeout")

    status_pushes = [
        push for push in _FakeTransport.pushes
        if push["payload"]["event_type"] == "chat.evolution_status"
    ]
    assert [push["payload"]["status"] for push in status_pushes] == ["start", "end"]
    assert status_pushes[-1]["payload"]["stage"] == "hidden"
    assert "timed out" in status_pushes[-1]["payload"]["message"]
    assert rail.cleanup_calls == 1


@pytest.mark.asyncio
async def test_normal_evolution_watcher_hides_timed_out_terminal_progress(monkeypatch):
    _FakeTransport.pushes = []
    adapter = _TestAdapter.build_with_rail(
        _FakeEvolutionRail([[_progress_event("timed out", stage="timed_out")]])
    )

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )

    await adapter.watch_evolution_and_push("stream-rid", "web", "sess-terminal-timeout")

    status_pushes = [
        push for push in _FakeTransport.pushes
        if push["payload"]["event_type"] == "chat.evolution_status"
    ]
    assert [push["payload"]["status"] for push in status_pushes] == ["start", "end"]
    assert status_pushes[-1]["payload"]["stage"] == "hidden"


@pytest.mark.asyncio
async def test_team_skill_evolve_approval_uses_record_api(monkeypatch):
    class _FakeTeamRail:
        def __init__(self) -> None:
            self.approved: list[str] = []
            self.rejected: list[str] = []

        async def approve_record(self, request_id: str) -> None:
            self.approved.append(request_id)

        async def reject_record(self, request_id: str) -> None:
            self.rejected.append(request_id)

    rail = _FakeTeamRail()
    adapter = object.__new__(JiuWenClawDeepAdapter)
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "find_team_skill_rail",
        staticmethod(lambda request_id, channel_id=None: rail),
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.sync_team_skills_across_managers",
        lambda session_id: None,
    )

    handled = await adapter.handle_team_skill_evolve_approval(
        "team_skill_evolve_req1",
        [{"selected_options": ["accept"]}],
        session_id="sess-1",
        channel_id="web",
    )

    assert handled is True
    assert rail.approved == ["team_skill_evolve_req1"]
    assert rail.rejected == []


@pytest.mark.asyncio
async def test_team_skill_evolve_approval_pushes_terminal_status(monkeypatch):
    class _FakeTeamRail:
        async def approve_record(self, request_id: str) -> None:
            return None

        async def reject_record(self, request_id: str) -> None:
            return None

    _FakeTransport.pushes = []
    adapter = object.__new__(JiuWenClawDeepAdapter)
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "find_team_skill_rail",
        staticmethod(lambda request_id, channel_id=None: _FakeTeamRail()),
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.sync_team_skills_across_managers",
        lambda session_id: None,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )

    handled = await adapter.handle_team_skill_evolve_approval(
        "team_skill_evolve_req1",
        [{"selected_options": ["接收"]}],
        session_id="sess-1",
        channel_id="web",
    )

    assert handled is True
    assert _FakeTransport.pushes == [
        {
            "session_id": "sess-1",
            "request_id": "team_skill_evolve_req1",
            "channel_id": "web",
            "payload": {
                "event_type": "chat.evolution_status",
                "request_id": "team_skill_evolve_req1",
                "status": "end",
                "stage": "completed",
                "message": "Team skill evolution accepted",
            },
        }
    ]
