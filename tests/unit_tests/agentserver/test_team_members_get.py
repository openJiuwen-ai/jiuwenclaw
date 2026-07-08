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


class _FakeMonitorHandler:
    """Minimal stand-in for TeamMonitorHandler."""

    def __init__(self, *, is_running: bool, snapshot: dict[str, Any] | None) -> None:
        self.is_running = is_running
        self._snapshot = snapshot

    async def get_team_snapshot(self) -> dict[str, Any] | None:
        return self._snapshot

    async def get_member_list(self) -> list[dict[str, Any]] | None:
        # /join 校验走 get_member_list（仅 members，无 tasks/team_id）；
        # 沿用 snapshot 里的 members 项以复用既有 fixture。
        if self._snapshot is None:
            return None
        return list(self._snapshot.get("members", []))


class _FakeTeamManager:
    def __init__(self, monitor_handler: _FakeMonitorHandler | None) -> None:
        self._monitor_handler = monitor_handler

    def get_monitor_handler(self, session_id: str) -> _FakeMonitorHandler | None:
        return self._monitor_handler


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)


def _make_request(session_id: str = "sess-1", channel_id: str = "feishu") -> AgentRequest:
    return AgentRequest(
        request_id="req-1",
        session_id=session_id,
        channel_id=channel_id,
        req_method=ReqMethod.TEAM_MEMBERS_GET,
        params={"session_id": session_id},
    )


async def _invoke(monitor_handler: _FakeMonitorHandler | None):
    """Call _handle_team_members_get without instantiating AgentWebSocketServer.

    Returns the decoded AgentResponse that the gateway would observe.
    """
    from jiuwenswarm.common.e2a.wire_codec import parse_agent_server_wire_unary
    from jiuwenswarm.server import agent_ws_server

    ws = _FakeWS()
    lock = asyncio.Lock()
    request = _make_request()
    fake_tm = _FakeTeamManager(monitor_handler)
    with mock.patch(
        "jiuwenswarm.agents.harness.team.get_team_manager",
        return_value=fake_tm,
    ):
        # handler body does not use `self`; call the underlying function.
        await agent_ws_server.AgentWebSocketServer._handle_team_members_get(
            None, ws, request, lock
        )
    assert len(ws.sent) == 1
    return parse_agent_server_wire_unary(json.loads(ws.sent[0]))


@pytest.mark.anyio
async def test_filters_only_human_agent_members() -> None:
    snapshot = {
        "members": [
            {"member_id": "leader-1", "name": "Leader", "role": "teammate"},
            {"member_id": "reviewer-1", "name": "Reviewer 1", "role": "human_agent"},
            {"member_id": "coder-1", "name": "Coder 1", "role": "teammate"},
            {"member_id": "pm-1", "name": "PM", "role": "human_agent"},
        ],
        "tasks": [{"task_id": "t-1"}],
        "team_id": "team-x",
    }
    resp = await _invoke(_FakeMonitorHandler(is_running=True, snapshot=snapshot))

    assert resp.request_id == "req-1"
    assert resp.ok is True
    payload = resp.payload
    # 成功路径只回 members（/join 不依赖 team_id/tasks，见 _handle_team_members_get）
    assert [m["member_id"] for m in payload["members"]] == ["reviewer-1", "pm-1"]
    assert "tasks" not in payload
    assert "team_id" not in payload


@pytest.mark.anyio
async def test_no_live_monitor_returns_empty_members() -> None:
    resp = await _invoke(monitor_handler=None)

    assert resp.ok is True
    assert resp.payload == {"members": [], "team_id": None}


@pytest.mark.anyio
async def test_cross_channel_finds_monitor_in_other_manager() -> None:
    """跨 channel /join：请求 channel 的 manager 无此 session，应遍历其他 manager 找到。

    场景：team 在 web channel 创建（monitor 注册在 web 的 TeamManager），飞书 /join
    发来的 request.channel_id=feishu。若只查 feishu 的 manager 会拿空列表误判"团队未就绪"；
    修复后应遍历所有 manager 命中 web 那个，返回真实 human_agent 成员。
    """
    snapshot = {
        "members": [
            {"member_id": "human-player-1", "role": "human_agent"},
            {"member_id": "ai-player-1", "role": "teammate"},
        ],
        "tasks": [],
        "team_id": "team-web",
    }
    live_handler = _FakeMonitorHandler(is_running=True, snapshot=snapshot)

    # feishu 的 manager（请求 channel）没有这个 session
    feishu_tm = _FakeTeamManager(monitor_handler=None)
    # web 的 manager 持有该 session 的 monitor
    web_tm = _FakeTeamManager(monitor_handler=live_handler)

    from jiuwenswarm.common.e2a.wire_codec import parse_agent_server_wire_unary
    from jiuwenswarm.server import agent_ws_server

    ws = _FakeWS()
    lock = asyncio.Lock()
    request = _make_request(session_id="sess-1", channel_id="feishu")
    with mock.patch(
        "jiuwenswarm.agents.harness.team.get_team_manager",
        return_value=feishu_tm,
    ), mock.patch(
        "jiuwenswarm.agents.harness.team.get_all_team_managers",
        return_value=[feishu_tm, web_tm],
    ):
        await agent_ws_server.AgentWebSocketServer._handle_team_members_get(
            None, ws, request, lock
        )
    assert len(ws.sent) == 1
    resp = parse_agent_server_wire_unary(json.loads(ws.sent[0]))

    assert resp.ok is True
    assert [m["member_id"] for m in resp.payload["members"]] == ["human-player-1"]


@pytest.mark.anyio
async def test_stopped_monitor_returns_empty_members() -> None:
    resp = await _invoke(_FakeMonitorHandler(is_running=False, snapshot=None))

    assert resp.ok is True
    assert resp.payload == {"members": [], "team_id": None}


@pytest.mark.anyio
async def test_snapshot_raises_returns_empty_members() -> None:
    class _BoomHandler(_FakeMonitorHandler):
        async def get_member_list(self) -> list[dict[str, Any]] | None:
            raise RuntimeError("boom")

    resp = await _invoke(_BoomHandler(is_running=True, snapshot=None))

    assert resp.ok is True
    # get_member_list 抛错时降级返回空 members（不含 team_id）
    assert resp.payload == {"members": []}


@pytest.mark.anyio
async def test_no_human_agent_seats_returns_empty_members() -> None:
    snapshot = {
        "members": [
            {"member_id": "leader-1", "role": "teammate"},
            {"member_id": "coder-1", "role": "teammate"},
        ],
        "tasks": [],
        "team_id": "team-y",
    }
    resp = await _invoke(_FakeMonitorHandler(is_running=True, snapshot=snapshot))

    assert resp.ok is True
    assert resp.payload["members"] == []
