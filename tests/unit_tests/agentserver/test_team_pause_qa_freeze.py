# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team pause: abort LLM first, then freeze leader QA (plan-aligned)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jiuwenclaw.agentserver.team.team_manager import TeamManager


class _FakeFreezeRail:
    def __init__(self, *, delay_sec: float = 0.0) -> None:
        self.calls: list[dict[str, Any]] = []
        self.delay_sec = delay_sec

    async def freeze_current_qa_sync(self, session_id: str, **kwargs: Any) -> None:
        self.calls.append({"session_id": session_id, **kwargs})
        if self.delay_sec:
            await asyncio.sleep(self.delay_sec)


class _FakeAbortHandle:
    def __init__(self) -> None:
        self.aborted = False

    def abort_llm_stream(self) -> None:
        self.aborted = True


@pytest.mark.asyncio
async def test_abort_team_llm_streams_cuts_leader_and_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = TeamManager()
    monkeypatch.setattr(mgr, "_resolve_session_team_name", lambda _sid: "team-1")

    abort_ctx = SimpleNamespace(request_abort_stream=lambda: setattr(abort_ctx, "hit", True))
    abort_ctx.hit = False
    active = SimpleNamespace(pause_requested=False, model_call_ctx=abort_ctx)
    harness = SimpleNamespace(active_round=active)
    member = _FakeAbortHandle()
    team = SimpleNamespace(
        resources=SimpleNamespace(harness=harness),
        spawn_manager=SimpleNamespace(spawned_handles={"m1": member}),
    )

    async def _resolve(_sid: str):
        return team

    monkeypatch.setattr(mgr, "_resolve_active_team_for_session", _resolve)

    ok = await mgr.abort_team_llm_streams_before_pause("sess-1", reason="t: ")
    assert ok is True
    assert active.pause_requested is True
    assert abort_ctx.hit is True
    assert member.aborted is True


@pytest.mark.asyncio
async def test_freeze_leader_qa_before_pause_calls_sync_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = TeamManager()
    monkeypatch.setattr(mgr, "_resolve_session_team_name", lambda _sid: "team-1")

    rail = _FakeFreezeRail()
    session = object()
    harness = SimpleNamespace(
        _registered_rails=[rail],
        _session=session,
    )
    team = SimpleNamespace(resources=SimpleNamespace(harness=harness), spawn_manager=None)

    async def _resolve(_sid: str):
        return team

    monkeypatch.setattr(mgr, "_resolve_active_team_for_session", _resolve)

    ok = await mgr.freeze_leader_qa_before_pause("sess-1", reason="t: ")
    assert ok is True
    assert len(rail.calls) == 1
    assert rail.calls[0]["session_id"] == "sess-1"
    assert rail.calls[0]["status"] == "interrupted"
    assert rail.calls[0]["persist_mode"] == "sync"
    assert rail.calls[0]["session"] is session


@pytest.mark.asyncio
async def test_freeze_leader_qa_timeout_does_not_block_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = TeamManager()
    monkeypatch.setattr(mgr, "_resolve_session_team_name", lambda _sid: "team-1")

    rail = _FakeFreezeRail(delay_sec=1.0)
    harness = SimpleNamespace(_registered_rails=[rail], _session=object())
    team = SimpleNamespace(resources=SimpleNamespace(harness=harness), spawn_manager=None)

    async def _resolve(_sid: str):
        return team

    monkeypatch.setattr(mgr, "_resolve_active_team_for_session", _resolve)

    ok = await mgr.freeze_leader_qa_before_pause(
        "sess-1", reason="t: ", timeout_sec=0.05
    )
    assert ok is False
    assert len(rail.calls) == 1
