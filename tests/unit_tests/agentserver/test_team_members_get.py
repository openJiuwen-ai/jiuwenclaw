# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for AgentWebSocketServer._handle_team_members_get (/join seat validation)."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest import mock

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)


def _make_request(
    session_id: str = "sess-1",
    channel_id: str = "feishu",
    team_name: str = "jiwen-team_sess-1",
) -> AgentRequest:
    return AgentRequest(
        request_id="req-1",
        session_id=session_id,
        channel_id=channel_id,
        req_method=ReqMethod.TEAM_MEMBERS_GET,
        params={"session_id": session_id, "team_name": team_name},
    )


async def _invoke(
    helpers_result: tuple[list[dict[str, Any]], str | None],
    team_name: str = "jiwen-team_sess-1",
    channel_id: str = "feishu",
):
    """Call _handle_team_members_get with ``query_team_human_members_for_join`` mocked."""
    from jiuwenswarm.common.e2a.wire_codec import parse_agent_server_wire_unary
    from jiuwenswarm.server import agent_ws_server

    ws = _FakeWS()
    lock = asyncio.Lock()
    request = _make_request(team_name=team_name, channel_id=channel_id)

    async def _stub(_session_id, _team_name):
        return helpers_result

    with mock.patch(
        "jiuwenswarm.server.runtime.agent_adapter.team_helpers"
        ".query_team_human_members_for_join",
        _stub,
    ):
        await agent_ws_server.AgentWebSocketServer._handle_team_members_get(
            None, ws, request, lock
        )
    assert len(ws.sent) == 1
    return parse_agent_server_wire_unary(json.loads(ws.sent[0]))


@pytest.mark.anyio
async def test_returns_members_and_team_name() -> None:
    """helpers 返回 members + team_name → server 包进 payload。"""
    members = [
        {"member_id": "reviewer-1", "role": "human_agent", "name": "r", "status": "ready",
         "execution_status": "idle", "mode": "build_mode"},
        {"member_id": "pm-1", "role": "human_agent", "name": "p", "status": "ready",
         "execution_status": "idle", "mode": "build_mode"},
    ]
    resp = await _invoke((members, "jiwen-team_sess-1"))

    assert resp.request_id == "req-1"
    assert resp.ok is True
    assert [m["member_id"] for m in resp.payload["members"]] == ["reviewer-1", "pm-1"]
    assert resp.payload["team_name"] == "jiwen-team_sess-1"


@pytest.mark.anyio
async def test_empty_members_team_name_none() -> None:
    """team_name 空或 helpers 返回空 → server 透传。"""
    resp = await _invoke(([], None), team_name="")

    assert resp.ok is True
    assert resp.payload == {"members": [], "team_name": None}


@pytest.mark.anyio
async def test_empty_members_with_team_name() -> None:
    """members 空但 team_name 有值 → server 仍回传 team_name（gateway 据此做一致性校验）。"""
    resp = await _invoke(([], "jiwen-team_sess-1"))

    assert resp.ok is True
    assert resp.payload == {"members": [], "team_name": "jiwen-team_sess-1"}
