# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests: plan interrupt path plus team runtime pause on cancel/pause."""

from __future__ import annotations

import pytest

from jiuwenclaw.agentserver.interface import JiuWenClaw
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse
from jiuwenclaw.schema.message import ReqMethod


class _InterruptHarness(JiuWenClaw):
    @property
    def session_manager_for_test(self):
        return getattr(self, "_session_manager")

    async def process_interrupt_for_test(self, request: AgentRequest):
        return await getattr(self, "_process_interrupt")(request)


class _FakeTeamManager:
    def __init__(self, *, has_runtime: bool = False) -> None:
        self.pause_calls: list[tuple[str, str]] = []
        self.cancel_calls: list[tuple[str, str]] = []
        self._has_runtime = has_runtime

    async def pause_session_runtime(self, session_id: str, reason: str = "") -> bool:
        self.pause_calls.append((session_id, reason))
        return True

    async def cancel_session_runtime(self, session_id: str, reason: str = "") -> bool:
        self.cancel_calls.append((session_id, reason))
        return True

    def has_stream_task(self, session_id: str) -> bool:
        return self._has_runtime

    def is_runtime_active(self, session_id: str) -> bool:
        return self._has_runtime

    def is_runtime_pending(self, session_id: str) -> bool:
        return False

    def is_session_initialized(self, session_id: str) -> bool:
        return self._has_runtime

    def is_pause_in_progress(self, session_id: str) -> bool:
        return False


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[AgentRequest] = []

    async def process_interrupt(self, request: AgentRequest) -> AgentResponse:
        self.calls.append(request)
        intent = request.params.get("intent", "cancel") if isinstance(request.params, dict) else "cancel"
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload={
                "event_type": "chat.interrupt_result",
                "intent": intent,
                "success": True,
                "message": f"adapter:{intent}",
            },
            metadata=request.metadata,
        )


def _build_interrupt_request(
    intent: str,
    *,
    session_id: str = "sess-1",
    mode: str | None = None,
) -> AgentRequest:
    # Mirror OfficeClaw: interrupt often has intent only (no mode/team).
    params: dict = {"intent": intent}
    if mode is not None:
        params["mode"] = mode
    return AgentRequest(
        request_id=f"req-{intent}",
        channel_id="officeclaw",
        session_id=session_id,
        req_method=ReqMethod.CHAT_CANCEL,
        params=params,
    )


@pytest.mark.asyncio
async def test_cancel_keeps_plan_adapter_and_pauses_team_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan cancel ≈ plan_pause; leftover team runtime still gets pause (not pool remove)."""
    claw = _InterruptHarness()
    fake_manager = _FakeTeamManager()
    fake_adapter = _FakeAdapter()
    cancelled_all: list[str] = []

    async def _fake_cancel_all(reason: str = "") -> None:
        cancelled_all.append(reason)

    async def _ensure_adapter():
        return fake_adapter

    monkeypatch.setattr(claw, "_ensure_adapter", _ensure_adapter)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.get_team_manager",
        lambda channel_id=None: fake_manager,
    )
    monkeypatch.setattr(claw.session_manager_for_test, "cancel_all_session_tasks", _fake_cancel_all)

    response = await claw.process_interrupt_for_test(_build_interrupt_request("cancel"))

    assert len(fake_adapter.calls) == 1
    assert response.payload["message"] == "adapter:cancel"
    assert fake_manager.pause_calls == [("sess-1", "interrupt(intent=cancel): ")]
    assert fake_manager.cancel_calls == []
    assert cancelled_all == ["interrupt(intent=cancel): "]


@pytest.mark.asyncio
async def test_team_mode_cancel_skips_plan_adapter_and_pauses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protocol stop-work/keep-team: no DeepAgent abort / plan_pause / dissolve."""
    claw = _InterruptHarness()
    fake_manager = _FakeTeamManager(has_runtime=True)
    fake_adapter = _FakeAdapter()
    cancelled_all: list[str] = []

    async def _fake_cancel_all(reason: str = "") -> None:
        cancelled_all.append(reason)

    async def _ensure_adapter():
        return fake_adapter

    monkeypatch.setattr(claw, "_ensure_adapter", _ensure_adapter)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.get_team_manager",
        lambda channel_id=None: fake_manager,
    )
    monkeypatch.setattr(claw.session_manager_for_test, "cancel_all_session_tasks", _fake_cancel_all)

    response = await claw.process_interrupt_for_test(
        _build_interrupt_request("cancel", mode="team")
    )

    assert fake_adapter.calls == []
    assert fake_manager.pause_calls == [("sess-1", "interrupt(intent=cancel): ")]
    assert fake_manager.cancel_calls == []
    assert cancelled_all == ["interrupt(intent=cancel): "]
    assert response.payload["event_type"] == "chat.interrupt_result"
    assert response.payload["team_paused"] is True
    assert "解散" not in str(response.payload.get("message") or "")


@pytest.mark.asyncio
async def test_team_runtime_without_mode_uses_pause_not_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relay may omit mode; active team runtime still takes protocol pause path."""
    claw = _InterruptHarness()
    fake_manager = _FakeTeamManager(has_runtime=True)
    fake_adapter = _FakeAdapter()

    async def _ensure_adapter():
        return fake_adapter

    monkeypatch.setattr(claw, "_ensure_adapter", _ensure_adapter)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.get_team_manager",
        lambda channel_id=None: fake_manager,
    )

    response = await claw.process_interrupt_for_test(_build_interrupt_request("pause"))

    assert fake_adapter.calls == []
    assert fake_manager.pause_calls == [("sess-1", "interrupt(intent=pause): ")]
    assert response.payload["team_paused"] is True


@pytest.mark.asyncio
async def test_pause_keeps_plan_adapter_then_pauses_team_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claw = _InterruptHarness()
    fake_manager = _FakeTeamManager()
    fake_adapter = _FakeAdapter()

    async def _ensure_adapter():
        return fake_adapter

    monkeypatch.setattr(claw, "_ensure_adapter", _ensure_adapter)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.get_team_manager",
        lambda channel_id=None: fake_manager,
    )

    response = await claw.process_interrupt_for_test(_build_interrupt_request("pause"))

    assert len(fake_adapter.calls) == 1
    assert response.payload["message"] == "adapter:pause"
    assert fake_manager.pause_calls == [("sess-1", "interrupt(intent=pause): ")]
    assert fake_manager.cancel_calls == []


@pytest.mark.asyncio
async def test_supplement_pauses_team_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    claw = _InterruptHarness()
    fake_manager = _FakeTeamManager()
    fake_adapter = _FakeAdapter()
    cancelled: list[tuple[str, str]] = []

    async def _fake_cancel_session(session_id: str, reason: str = "") -> None:
        cancelled.append((session_id, reason))

    async def _ensure_adapter():
        return fake_adapter

    monkeypatch.setattr(claw, "_ensure_adapter", _ensure_adapter)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.get_team_manager",
        lambda channel_id=None: fake_manager,
    )
    monkeypatch.setattr(claw.session_manager_for_test, "cancel_session_task", _fake_cancel_session)

    response = await claw.process_interrupt_for_test(_build_interrupt_request("supplement"))

    assert response.payload["message"] == "adapter:supplement"
    assert fake_manager.pause_calls == [("sess-1", "interrupt(intent=supplement): ")]
    assert fake_manager.cancel_calls == []
    assert cancelled == [("sess-1", "interrupt(supplement): ")]


@pytest.mark.asyncio
async def test_resume_uses_plan_adapter_only(monkeypatch: pytest.MonkeyPatch) -> None:
    claw = _InterruptHarness()
    fake_manager = _FakeTeamManager()
    fake_adapter = _FakeAdapter()

    async def _ensure_adapter():
        return fake_adapter

    monkeypatch.setattr(claw, "_ensure_adapter", _ensure_adapter)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.get_team_manager",
        lambda channel_id=None: fake_manager,
    )

    response = await claw.process_interrupt_for_test(_build_interrupt_request("resume"))

    assert len(fake_adapter.calls) == 1
    assert response.payload["message"] == "adapter:resume"
    assert fake_manager.pause_calls == []
    assert fake_manager.cancel_calls == []
