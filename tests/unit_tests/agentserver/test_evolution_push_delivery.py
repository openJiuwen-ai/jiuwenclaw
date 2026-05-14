# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenclaw.server.runtime.agent_adapter import interface_deep as interface_deep_module
from jiuwenclaw.server.runtime.agent_adapter.interface_deep import JiuWenClawDeepAdapter


class _FakeTransport:
    pushes: list[dict] = []

    def __init__(self):
        self.pushes = self.__class__.pushes

    async def send_push(self, payload: dict) -> None:
        self.pushes.append(payload)


class _FakeEvolutionRail:
    @staticmethod
    async def drain_pending_approval_events(
        wait: bool = False,
        timeout: float | None = None,
    ):
        return [
            SimpleNamespace(
                type="chat.ask_user_question",
                payload={"request_id": "team_skill_evolve_req1", "questions": [{"header": "x"}]},
            )
        ]

    @staticmethod
    def drain_evolution_outcomes():
        return [{"status": "completed", "message": "done"}]

    @staticmethod
    async def cleanup_background_tasks() -> None:
        return None


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
        "jiuwenclaw.server.gateway_push.WebSocketGatewayPushTransport",
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
