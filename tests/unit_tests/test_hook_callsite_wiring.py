# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""调用点接线验证：确认真实运行路径确实调用了 hook 触发器.

- SubagentStop: `_stop_dynamic_member_agent` 停止动态成员时调用 RailHookEmitter
  的 SUBAGENT_STOP（验证非 Rail 层事件的真实 emit 路径已接通）。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import jiuwenswarm.agents.harness.team.remote_member_bootstrap as rmb
import jiuwenswarm.server.hooks.rail_hook_emitter as em_mod
from jiuwenswarm.common.hooks_config import HookEvent


@pytest.mark.asyncio
async def test_stop_dynamic_member_agent_fires_subagent_stop(monkeypatch):
    """_stop_dynamic_member_agent 停止成员时触发 SubagentStop hook."""
    sid, member = "s-stop-wire", "calc-expert"
    fake_agent = object()
    rmb._DYNAMIC_MEMBER_AGENTS[(sid, member)] = fake_agent
    rmb._DYNAMIC_MEMBER_INVOKE_TASKS.pop((sid, member), None)

    # 避免真正的运行时停止逻辑（会访问 agent 属性）
    monkeypatch.setattr(rmb, "_stop_team_agent_runtime", AsyncMock(return_value=None))

    calls: list[tuple] = []

    class _FakeEmitter:
        def trigger(self, event, query="", hook_input=None, session_id=""):
            calls.append((event, query, hook_input, session_id))

    monkeypatch.setattr(em_mod, "get_rail_hook_emitter", lambda: _FakeEmitter())

    try:
        ok = await rmb._stop_dynamic_member_agent(sid, member)
    finally:
        rmb._DYNAMIC_MEMBER_AGENTS.pop((sid, member), None)

    assert ok is True
    assert len(calls) == 1
    event, query, hook_input, session_id = calls[0]
    assert event is HookEvent.SUBAGENT_STOP
    assert query == member
    assert session_id == sid
    assert hook_input["member_name"] == member


@pytest.mark.asyncio
async def test_stop_dynamic_member_agent_no_agent_does_not_fire(monkeypatch):
    """无成员运行时（agent is None）时不应触发 SubagentStop（避免误报）."""
    sid, member = "s-stop-none", "nope"
    rmb._DYNAMIC_MEMBER_AGENTS.pop((sid, member), None)
    rmb._DYNAMIC_MEMBER_INVOKE_TASKS.pop((sid, member), None)

    calls: list[tuple] = []

    class _FakeEmitter:
        def trigger(self, event, query="", hook_input=None, session_id=""):
            calls.append(event)

    monkeypatch.setattr(em_mod, "get_rail_hook_emitter", lambda: _FakeEmitter())

    ok = await rmb._stop_dynamic_member_agent(sid, member)
    assert ok is False
    assert calls == []
