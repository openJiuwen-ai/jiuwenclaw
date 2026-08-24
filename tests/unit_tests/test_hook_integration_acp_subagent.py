# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""集成测试：验证两条无法在纯单元层驱动的真实 emit 路径.

1. Permission ACP 路径：`build_permission_rail` 内的 `_request_permission_confirmation`
   闭包——构建真实 PermissionInterruptRail，取回 host 回调，mock ACP 输出管理器，
   验证 PermissionRequest（必发）与 PermissionDenied（拒绝/取消时发、允许时不发）。
2. SubagentStart 路径：`install_subagent_observability_hook` monkey-patch
   `DeepAgent.create_subagent`——用桩替换 original，调用安装器，再调用
   `DeepAgent.create_subagent`，验证 SubagentStart 经 emitter 触发。
"""

from __future__ import annotations

import pytest

import jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers as ih
import jiuwenswarm.agents.harness.common.tools.acp_output_tools as acp_tools
import jiuwenswarm.server.hooks.rail_hook_emitter as em_mod
from jiuwenswarm.agents.harness.common.rails.permissions.tool_permission_context import (
    TOOL_PERMISSION_CHANNEL_ID,
)
from jiuwenswarm.common.hooks_config import HookEvent

from openjiuwen.harness.security.host import PermissionConfirmationRequest
from openjiuwen.harness.security.models import PermissionLevel, PermissionResult


# ---- 共用 recording emitter ----

class _Recorder:
    def __init__(self):
        self.calls: list[tuple] = []

    def trigger(self, event, query="", hook_input=None, session_id=""):
        self.calls.append((event, query, hook_input, session_id))

    def of(self, event) -> list[tuple]:
        return [c for c in self.calls if c[0] is event]


# ============================================================
# Path 1: Permission ACP 路径
# ============================================================

def _fake_ctx(session_id: str = "s-acp"):
    sess = type("S", (), {"get_session_id": lambda self: session_id})()
    return type("Ctx", (), {"session": sess})()


def _fake_tool_call(name: str = "Bash"):
    return type("TC", (), {"name": name, "arguments": {"cmd": "ls"}, "id": "tc1"})()


def _make_req() -> PermissionConfirmationRequest:
    return PermissionConfirmationRequest(
        ctx=_fake_ctx(),
        tool_call=_fake_tool_call(),
        result=PermissionResult(permission=PermissionLevel.ASK, reason="needs approval"),
        auto_confirm_key="bash:allow",
    )


class _FakeACPManager:
    def __init__(self, response):
        self._response = response

    async def send_jsonrpc_request(self, method, params, session_id=None):
        return self._response


def _build_rail():
    return ih.build_permission_rail({"permissions": {"enabled": True}})


@pytest.mark.asyncio
async def test_acp_reject_fires_request_and_denied(monkeypatch):
    rail = _build_rail()
    host = rail._host
    rec = _Recorder()
    monkeypatch.setattr(em_mod, "get_rail_hook_emitter", lambda: rec)
    monkeypatch.setattr(
        acp_tools, "get_acp_output_manager",
        lambda: _FakeACPManager({"result": {"outcome": {"outcome": "selected", "optionId": "reject-once"}}}),
    )
    token = TOOL_PERMISSION_CHANNEL_ID.set("acp")
    try:
        resp = await host.request_permission_confirmation(_make_req())
    finally:
        TOOL_PERMISSION_CHANNEL_ID.reset(token)

    # PermissionRequest 必发（请求发出时刻）
    req_calls = rec.of(HookEvent.PERMISSION_REQUEST)
    assert len(req_calls) == 1
    assert req_calls[0][1] == "Bash"        # query = tool_name
    assert req_calls[0][3] == "s-acp"      # session_id

    # PermissionDenied 发（用户拒绝）
    denied = rec.of(HookEvent.PERMISSION_DENIED)
    assert len(denied) == 1
    assert denied[0][1] == "Bash"

    # 返回拒绝响应
    assert resp is not None and resp.approved is False


@pytest.mark.asyncio
async def test_acp_cancelled_fires_denied(monkeypatch):
    rail = _build_rail()
    host = rail._host
    rec = _Recorder()
    monkeypatch.setattr(em_mod, "get_rail_hook_emitter", lambda: rec)
    monkeypatch.setattr(
        acp_tools, "get_acp_output_manager",
        lambda: _FakeACPManager({"result": {"outcome": {"outcome": "cancelled"}}}),
    )
    token = TOOL_PERMISSION_CHANNEL_ID.set("acp")
    try:
        await host.request_permission_confirmation(_make_req())
    finally:
        TOOL_PERMISSION_CHANNEL_ID.reset(token)

    assert rec.of(HookEvent.PERMISSION_REQUEST)
    assert rec.of(HookEvent.PERMISSION_DENIED)


@pytest.mark.asyncio
async def test_acp_allow_fires_request_but_not_denied(monkeypatch):
    rail = _build_rail()
    host = rail._host
    rec = _Recorder()
    monkeypatch.setattr(em_mod, "get_rail_hook_emitter", lambda: rec)
    monkeypatch.setattr(
        acp_tools, "get_acp_output_manager",
        lambda: _FakeACPManager({"result": {"outcome": {"outcome": "selected", "optionId": "allow-once"}}}),
    )
    token = TOOL_PERMISSION_CHANNEL_ID.set("acp")
    try:
        resp = await host.request_permission_confirmation(_make_req())
    finally:
        TOOL_PERMISSION_CHANNEL_ID.reset(token)

    assert rec.of(HookEvent.PERMISSION_REQUEST)
    assert not rec.of(HookEvent.PERMISSION_DENIED)
    assert resp is not None and resp.approved is True


@pytest.mark.asyncio
async def test_non_acp_channel_fires_request_returns_interrupt(monkeypatch):
    """非 ACP 渠道也触发 PermissionRequest（走 interrupt 回退），不触发 Denied."""
    rail = _build_rail()
    host = rail._host
    rec = _Recorder()
    monkeypatch.setattr(em_mod, "get_rail_hook_emitter", lambda: rec)
    token = TOOL_PERMISSION_CHANNEL_ID.set("web")
    try:
        resp = await host.request_permission_confirmation(_make_req())
    finally:
        TOOL_PERMISSION_CHANNEL_ID.reset(token)

    assert resp == "interrupt"
    assert rec.of(HookEvent.PERMISSION_REQUEST)
    assert not rec.of(HookEvent.PERMISSION_DENIED)


# ============================================================
# Path 2: SubagentStart 路径（create_subagent monkey-patch）
# ============================================================

def test_install_and_create_subagent_fires_subagent_start(monkeypatch):
    from openjiuwen.harness.deep_agent import DeepAgent
    from jiuwenswarm.agents.harness import agent_observability as ao

    real = DeepAgent.create_subagent
    stub_sub = object()

    def stub(self, *args, **kwargs):  # noqa: ARG001
        return stub_sub

    # 用桩替换 original，避免真正创建子 agent
    DeepAgent.create_subagent = stub
    # attach_subagent_observability 在桩 subagent 上会失败，置为 no-op
    monkeypatch.setattr(ao, "attach_subagent_observability", lambda sub: None)
    rec = _Recorder()
    monkeypatch.setattr(em_mod, "get_rail_hook_emitter", lambda: rec)

    try:
        ao.install_subagent_observability_hook()
        DeepAgent.create_subagent(object(), "general-purpose", "sub-1")
    finally:
        DeepAgent.create_subagent = real

    starts = rec.of(HookEvent.SUBAGENT_START)
    assert len(starts) == 1
    assert starts[0][1] == "general-purpose"   # query = subagent_type
    assert starts[0][3] == "sub-1"             # session_id = subsession_id


def test_subagent_start_not_fired_when_install_skipped(monkeypatch):
    """未安装 hook 时 create_subagent 不应触发 SubagentStart."""
    from openjiuwen.harness.deep_agent import DeepAgent
    from jiuwenswarm.agents.harness import agent_observability as ao

    real = DeepAgent.create_subagent
    stub_sub = object()
    DeepAgent.create_subagent = lambda self, *a, **k: stub_sub  # noqa: E731
    monkeypatch.setattr(ao, "attach_subagent_observability", lambda sub: None)
    rec = _Recorder()
    monkeypatch.setattr(em_mod, "get_rail_hook_emitter", lambda: rec)

    try:
        # 不调用 install_subagent_observability_hook —— 直接调 stub
        DeepAgent.create_subagent(object(), "general-purpose", "sub-2")
    finally:
        DeepAgent.create_subagent = real

    assert rec.of(HookEvent.SUBAGENT_START) == []
