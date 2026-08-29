# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""HITL resume should re-invoke skill_acceleration_exec so ReAct can summarize."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY
from jiuwenswarm.agents.harness.common.rails.stream_event_rail import (
    JiuSwarmStreamEventRail,
)
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter
from jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools import (
    _resolve_skill_turbo_resume_session_id,
    _resume_user_input_from_raw,
    get_skill_turbo_resume_answers,
    reset_skill_turbo_resume_answers,
    set_skill_turbo_resume_answers,
)


class _StreamSession:
    def __init__(self):
        self.chunks = []

    async def write_stream(self, chunk):
        self.chunks.append(chunk)


def _tool_ctx(session, tool_name: str):
    from openjiuwen.core.single_agent.rail.base import ToolCallInputs

    tool_call = SimpleNamespace(id="call-1", name=tool_name, arguments={})
    return SimpleNamespace(
        session=session,
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name=tool_name,
            tool_args={},
            tool_result={"success": True},
        ),
        extra={},
        exception=None,
    )


def test_deep_agent_has_skill_turbo_interrupt():
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = None
    assert adapter._deep_agent_has_skill_turbo_interrupt() is False

    adapter._instance = SimpleNamespace(
        _loop_session=SimpleNamespace(
            get_state=lambda _key: SimpleNamespace(
                interrupted_tools={
                    "call_1": SimpleNamespace(
                        tool_call=SimpleNamespace(name="skill_acceleration_exec")
                    )
                }
            )
        )
    )
    assert adapter._deep_agent_has_skill_turbo_interrupt() is True

    adapter._instance._loop_session.get_state = lambda _key: SimpleNamespace(
        interrupted_tools={
            "call_1": SimpleNamespace(tool_call=SimpleNamespace(name="ask_user"))
        }
    )
    assert adapter._deep_agent_has_skill_turbo_interrupt() is False


@pytest.mark.asyncio
async def test_try_skill_turbo_resume_defers_when_outer_interrupt_present():
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = SimpleNamespace(
        _loop_session=SimpleNamespace(
            get_state=lambda _key: SimpleNamespace(
                interrupted_tools={
                    "call_1": SimpleNamespace(
                        tool_call=SimpleNamespace(name="skill_acceleration_exec")
                    )
                }
            )
        )
    )
    request = AgentRequest(
        request_id="req-1",
        channel_id="officeclaw",
        session_id="sess-1",
        req_method=ReqMethod.CHAT_SEND,
        params={
            "answers": [{"question": "页数", "selected_options": ["10"]}],
            "source": "ask_user_interrupt",
        },
    )
    assert await adapter._try_skill_turbo_resume(request, {}) is None


def test_resume_user_input_from_interactive_input():
    interactive = InteractiveInput()
    payload = {
        "status": "answered",
        "answers": [{"question": "页数", "selected_options": ["10"]}],
    }
    interactive.update("call_outer", payload)
    assert _resume_user_input_from_raw(interactive, {}, None) is payload


def test_resume_user_input_from_raw_answers_list():
    answers = [{"question": "页数", "selected_options": ["10"]}]
    adapter = SimpleNamespace(
        _skill_turbo_answers_to_confirm_payload=lambda raw, _ctx: raw
    )
    assert _resume_user_input_from_raw(answers, {}, adapter) == answers


def test_resume_answers_contextvar_roundtrip():
    token = set_skill_turbo_resume_answers(["a"])
    try:
        assert get_skill_turbo_resume_answers() == ["a"]
    finally:
        reset_skill_turbo_resume_answers(token)
    assert get_skill_turbo_resume_answers() is None


def test_resolve_skill_turbo_resume_session_id_prefers_metadata():
    parent = SimpleNamespace(get_session_id=lambda: "parent-sid")
    assert _resolve_skill_turbo_resume_session_id("meta-sid", parent) == "meta-sid"


def test_resolve_skill_turbo_resume_session_id_falls_back_to_parent():
    parent = SimpleNamespace(get_session_id=lambda: "parent-sid")
    assert _resolve_skill_turbo_resume_session_id("", parent) == "parent-sid"
    assert _resolve_skill_turbo_resume_session_id(None, parent) == "parent-sid"
    assert _resolve_skill_turbo_resume_session_id("", None) == ""


@pytest.mark.asyncio
async def test_skill_acceleration_exec_resume_skips_duplicate_tool_call_emit():
    rail = JiuSwarmStreamEventRail()
    session = _StreamSession()
    ctx = _tool_ctx(session, "skill_acceleration_exec")
    ctx.extra[RESUME_USER_INPUT_KEY] = [{"question": "q", "selected_options": ["a"]}]
    await rail.before_tool_call(ctx)
    assert not any(getattr(chunk, "type", None) == "tool_call" for chunk in session.chunks)
    assert not any(getattr(chunk, "type", None) == "tool_update" for chunk in session.chunks)
    await rail.after_tool_call(ctx)


@pytest.mark.asyncio
async def test_skill_acceleration_exec_first_invoke_still_emits_tool_call():
    rail = JiuSwarmStreamEventRail()
    session = _StreamSession()
    ctx = _tool_ctx(session, "skill_acceleration_exec")
    await rail.before_tool_call(ctx)
    assert any(getattr(chunk, "type", None) == "tool_call" for chunk in session.chunks)
    await rail.after_tool_call(ctx)
