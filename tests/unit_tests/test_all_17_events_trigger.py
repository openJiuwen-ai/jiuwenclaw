# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""端到端触发验证：逐一确认 17 个 HookEvent 都能从对应触发点真正拉起子进程执行.

每个事件配置一个真实 command hook（`echo <EVENT> > <marker>`），从该事件
对应的触发点（UserHookRail 回调 / GatewayHookHandler 方法 / RailHookEmitter）
调用，然后断言标记文件被创建且内容为事件名 —— 证明 HookExecutor 真的
spawn 了子进程并执行了用户配置的脚本。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

import pytest

from jiuwenswarm.common.hooks_config import HookEvent, HookMatcher, HooksConfig
from jiuwenswarm.gateway.hooks.handler import GatewayHookHandler
from jiuwenswarm.server.hooks.rail_hook_emitter import RailHookEmitter
from jiuwenswarm.server.hooks.user_hook_rail import UserHookRail


# ---- mock ctx inputs ----

@dataclass
class _ToolInputs:
    tool_name: str = ""
    tool_args: Any = None
    tool_result: Any = None
    tool_msg: Any = None


@dataclass
class _ModelCallInputs:
    messages: Any = None
    tools: Any = None
    response: Any = None


@dataclass
class _Ctx:
    inputs: Any = field(default_factory=_ToolInputs)
    extra: dict = field(default_factory=dict)
    session: Any = None
    exception: Any = None


# ---- helpers ----

def _marker(event: str) -> str:
    return f".verify_{event}.tmp"


def _cmd(event: str) -> dict:
    return {"command": f"echo {event} > {_marker(event)}", "timeout": 15}


def _cfg(event: HookEvent, matcher: str = "*") -> HooksConfig:
    return HooksConfig(events={
        event.value: [HookMatcher(matcher=matcher, hooks=[_cmd(event.value)])]
    })


def _assert_marker(event: str) -> None:
    m = _marker(event)
    try:
        assert os.path.exists(m), f"[{event}] marker not created — hook did NOT fire"
        content = open(m, encoding="utf-8").read().strip()
        assert content == event, f"[{event}] marker content mismatch: {content!r}"
    finally:
        if os.path.exists(m):
            os.remove(m)


# ============================================================
# Rail 层（6 个）—— UserHookRail 回调
# ============================================================

class TestRailEventsTrigger:
    @pytest.mark.asyncio
    async def test_PreToolUse(self):
        rail = UserHookRail(_cfg(HookEvent.PRE_TOOL_USE))
        await rail.before_tool_call(_Ctx(inputs=_ToolInputs(tool_name="Bash")))
        _assert_marker("PreToolUse")

    @pytest.mark.asyncio
    async def test_PostToolUse(self):
        rail = UserHookRail(_cfg(HookEvent.POST_TOOL_USE))
        await rail.after_tool_call(_Ctx(inputs=_ToolInputs(tool_name="Bash")))
        _assert_marker("PostToolUse")

    @pytest.mark.asyncio
    async def test_PostToolUseFailure(self):
        rail = UserHookRail(_cfg(HookEvent.POST_TOOL_USE_FAILURE))
        await rail.on_tool_exception(_Ctx(inputs=_ToolInputs(tool_name="Bash")))
        _assert_marker("PostToolUseFailure")

    @pytest.mark.asyncio
    async def test_Stop(self):
        rail = UserHookRail(_cfg(HookEvent.STOP))
        await rail.after_invoke(_Ctx())
        _assert_marker("Stop")

    @pytest.mark.asyncio
    async def test_BeforeModelCall(self):
        rail = UserHookRail(_cfg(HookEvent.BEFORE_MODEL_CALL))
        await rail.before_model_call(_Ctx(inputs=_ModelCallInputs(messages=[])))
        _assert_marker("BeforeModelCall")

    @pytest.mark.asyncio
    async def test_AfterModelCall(self):
        rail = UserHookRail(_cfg(HookEvent.AFTER_MODEL_CALL))
        await rail.after_model_call(_Ctx(inputs=_ModelCallInputs(messages=[])))
        _assert_marker("AfterModelCall")


# ============================================================
# Gateway 层（7 个）—— GatewayHookHandler 方法
# ============================================================

class TestGatewayEventsTrigger:
    @pytest.mark.asyncio
    async def test_SessionStart(self):
        h = GatewayHookHandler(_cfg(HookEvent.SESSION_START))
        await h.on_session_start("s1", source="web")
        _assert_marker("SessionStart")

    @pytest.mark.asyncio
    async def test_UserPromptSubmit(self):
        h = GatewayHookHandler(_cfg(HookEvent.USER_PROMPT_SUBMIT))
        await h.on_user_prompt_submit("s1", "hello")
        _assert_marker("UserPromptSubmit")

    @pytest.mark.asyncio
    async def test_SessionEnd(self):
        h = GatewayHookHandler(_cfg(HookEvent.SESSION_END))
        await h.on_session_end("s1", reason="delete")
        _assert_marker("SessionEnd")

    @pytest.mark.asyncio
    async def test_Notification(self):
        h = GatewayHookHandler(_cfg(HookEvent.NOTIFICATION))
        await h.on_notification("notice", "msg", session_id="s1")
        _assert_marker("Notification")

    @pytest.mark.asyncio
    async def test_ConfigChange(self):
        h = GatewayHookHandler(_cfg(HookEvent.CONFIG_CHANGE))
        await h.on_config_change(["models.defaults"])
        _assert_marker("ConfigChange")

    @pytest.mark.asyncio
    async def test_InstructionsLoaded(self):
        h = GatewayHookHandler(_cfg(HookEvent.INSTRUCTIONS_LOADED))
        await h.on_instructions_loaded(source="AGENTS.md")
        _assert_marker("InstructionsLoaded")

    @pytest.mark.asyncio
    async def test_Setup(self):
        h = GatewayHookHandler(_cfg(HookEvent.SETUP))
        await h.on_setup(source="gateway")
        _assert_marker("Setup")


# ============================================================
# Rail 发射器层（4 个）—— RailHookEmitter（Permission / Subagent）
# ============================================================

class TestEmitterEventsTrigger:
    @pytest.mark.asyncio
    async def test_PermissionRequest(self):
        em = RailHookEmitter(_cfg(HookEvent.PERMISSION_REQUEST))
        await em.fire(HookEvent.PERMISSION_REQUEST, query="Bash")
        _assert_marker("PermissionRequest")

    @pytest.mark.asyncio
    async def test_PermissionDenied(self):
        em = RailHookEmitter(_cfg(HookEvent.PERMISSION_DENIED))
        await em.fire(HookEvent.PERMISSION_DENIED, query="Bash")
        _assert_marker("PermissionDenied")

    @pytest.mark.asyncio
    async def test_SubagentStart(self):
        em = RailHookEmitter(_cfg(HookEvent.SUBAGENT_START))
        await em.fire(HookEvent.SUBAGENT_START, query="general-purpose")
        _assert_marker("SubagentStart")

    @pytest.mark.asyncio
    async def test_SubagentStop(self):
        em = RailHookEmitter(_cfg(HookEvent.SUBAGENT_STOP))
        await em.fire(HookEvent.SUBAGENT_STOP, query="member-1")
        _assert_marker("SubagentStop")


# ============================================================
# 汇总：17 个事件全部覆盖
# ============================================================

class TestAll17EventsCovered:
    def test_exactly_17_events(self):
        assert len(list(HookEvent)) == 17

    def test_every_event_has_a_trigger_test(self):
        """每个事件在本文件中都有对应的真实触发用例."""
        import re
        src = open(__file__, encoding="utf-8").read()
        missing = []
        for ev in HookEvent:
            if not re.search(rf"async def test_{ev.value}\b", src):
                missing.append(ev.value)
        assert not missing, f"缺少触发用例的事件: {missing}"
