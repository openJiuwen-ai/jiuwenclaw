# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for Team-mode chat.steer routing.

Team steering reaches the leader's in-flight round. Member-addressed and
broadcast messages are not steering and keep using the ordinary Team
interaction router, so this path must never be reached for them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm


def _facade(session_id: str = "sess-team") -> JiuWenSwarm:
    """Bypass the heavy __init__; only session resolution is needed."""
    facade = JiuWenSwarm.__new__(JiuWenSwarm)
    facade._session_manager = MagicMock()
    facade._session_manager.get_session_id.return_value = session_id
    return facade


def _req(query: object = "prefer the async client", mode: str = "team") -> AgentRequest:
    return AgentRequest(
        request_id="req-team-steer",
        channel_id="web",
        session_id="sess-team",
        req_method=ReqMethod.CHAT_STEER,
        params={"query": query, "mode": mode},
    )


def _manager(ok: bool, reason: str | None = None) -> MagicMock:
    manager = MagicMock()
    manager.steer_leader = AsyncMock(return_value=(ok, reason))
    return manager


@pytest.mark.anyio
async def test_accepted_team_steer_targets_the_leader() -> None:
    manager = _manager(True)
    with patch("jiuwenswarm.agents.harness.team.get_team_manager", return_value=manager):
        resp = await _facade()._process_team_steer(_req())

    assert resp.ok is True
    assert resp.payload["accepted"] is True
    # No event_type -- an ACK carrying one is converted to an event frame and the
    # client's awaited RPC never resolves.
    assert "event_type" not in resp.payload
    # The field that lets a client tell which round its text reached.
    assert resp.payload["target"] == "team_leader"
    assert resp.payload["disposition"] == "steer_queued"
    # The request id must reach the runtime: chat.steer_applied builds `dropped`
    # from it, so omitting it makes a rail-removed Team steer look applied.
    manager.steer_leader.assert_awaited_once_with(
        "sess-team", "prefer the async client", steer_id="req-team-steer"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "reason", ["not_active", "no_active_round", "gate_closed", "missing_target"]
)
async def test_runtime_rejections_are_reported_verbatim(reason: str) -> None:
    """The reason token is passed through, not collapsed into a generic error."""
    manager = _manager(False, reason)
    with patch("jiuwenswarm.agents.harness.team.get_team_manager", return_value=manager):
        resp = await _facade()._process_team_steer(_req())

    # ok stays true: the request was understood and answered.
    assert resp.ok is True
    assert resp.payload["accepted"] is False
    assert resp.payload["reason"] == reason
    assert resp.payload["target"] == "team_leader"
    # A rejection has no disposition -- nothing was queued to describe.
    assert "disposition" not in resp.payload


@pytest.mark.anyio
@pytest.mark.parametrize("bad_query", ["", "   ", None, 42])
async def test_empty_team_steer_never_reaches_the_runtime(bad_query: object) -> None:
    manager = _manager(True)
    with patch("jiuwenswarm.agents.harness.team.get_team_manager", return_value=manager):
        resp = await _facade()._process_team_steer(_req(bad_query))

    assert resp.payload["accepted"] is False
    assert resp.payload["reason"] == "empty_query"
    manager.steer_leader.assert_not_awaited()


@pytest.mark.anyio
async def test_a_missing_reason_still_produces_a_stable_token() -> None:
    """A runtime that fails without saying why must not yield reason=None."""
    manager = _manager(False, None)
    with patch("jiuwenswarm.agents.harness.team.get_team_manager", return_value=manager):
        resp = await _facade()._process_team_steer(_req())

    assert resp.payload["reason"] == "steer_failed"


@pytest.mark.anyio
async def test_an_accepted_team_steer_is_written_to_history(monkeypatch) -> None:
    """Otherwise the text survives only until the session is reloaded.

    The leader visibly changes course mid-round and, after a reload, the reason
    is gone from the transcript.
    """
    calls: list[dict] = []
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface.append_history_record",
        lambda **kw: calls.append(kw),
    )
    manager = _manager(True)
    with patch("jiuwenswarm.agents.harness.team.get_team_manager", return_value=manager):
        await _facade()._process_team_steer(_req())

    assert len(calls) == 1
    assert calls[0]["role"] == "user"
    assert calls[0]["content"] == "prefer the async client"
    assert calls[0]["extra"] == {"input_kind": "steer_queued", "target": "team_leader"}


@pytest.mark.anyio
@pytest.mark.parametrize("reason", ["no_active_round", "not_active", "gate_closed"])
async def test_a_rejected_team_steer_is_never_written_to_history(
    monkeypatch, reason: str
) -> None:
    """A rejected steer changed nothing, so recording it would invent a turn."""
    calls: list[dict] = []
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface.append_history_record",
        lambda **kw: calls.append(kw),
    )
    manager = _manager(False, reason)
    with patch("jiuwenswarm.agents.harness.team.get_team_manager", return_value=manager):
        await _facade()._process_team_steer(_req())

    assert calls == []


@pytest.mark.anyio
async def test_an_empty_team_steer_is_never_written_to_history(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface.append_history_record",
        lambda **kw: calls.append(kw),
    )
    manager = _manager(True)
    with patch("jiuwenswarm.agents.harness.team.get_team_manager", return_value=manager):
        await _facade()._process_team_steer(_req(""))

    assert calls == []


def test_the_two_targets_are_distinguishable() -> None:
    """Single-agent and Team answer the same event with different targets.

    Without this a client cannot tell whether its text reached an agent round or
    a leader round, which are different things happening in different places.
    """
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )

    team = JiuWenSwarm._team_steer_ack(_req(), accepted=True, reason=None)
    agent = JiuWenSwarmDeepAdapter._steer_ack(
        _req(), accepted=True, reason=None, disposition="steer_queued"
    )

    assert team.payload["target"] == "team_leader"
    assert agent.payload["target"] == "agent"
    # Same correlation field, so one client parser handles both.
    assert team.payload["request_id"] == agent.payload["request_id"]
    # And neither declares an event_type. A client that worked in Agent mode and
    # hung in Team mode would be worse than one that hung in both, so this is
    # asserted across the pair rather than once per builder.
    assert "event_type" not in team.payload
    assert "event_type" not in agent.payload
