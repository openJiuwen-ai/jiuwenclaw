# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""HITL resume should re-invoke skill_acceleration_exec so ReAct can summarize."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY
from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from jiuwenswarm.agents.harness.common.rails.stream_event_rail import (
    JiuSwarmStreamEventRail,
)
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter
from jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools import (
    _SKILL_TURBO_HITL_PLACEHOLDER,
    _SKILL_TURBO_STOP_HINT,
    _resolve_skill_turbo_resume_session_id,
    _resume_user_input_from_raw,
    get_skill_turbo_resume_answers,
    reset_skill_turbo_resume_answers,
    set_skill_turbo_hitl_tic,
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
        "answers": [
            {"question": "受众", "selected_options": ["企业高管"]},
            {"question": "目的", "selected_options": ["工作汇报"]},
        ],
    }
    interactive.update("call_outer", payload)
    got = _resume_user_input_from_raw(interactive, {}, None)
    assert got is payload
    assert len(got["answers"]) == 2


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


def _ask_user_request(source: str) -> AgentRequest:
    return AgentRequest(
        request_id="req-1",
        channel_id="officeclaw",
        session_id="sess-1",
        req_method=ReqMethod.CHAT_SEND,
        params={
            "answers": [{"selected_options": ["本次允许"]}],
            "source": source,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["permission_interrupt", "confirm_interrupt", ""])
async def test_try_skill_turbo_resume_ignores_non_ask_user_answers(source):
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = SimpleNamespace(card=object(), _loop_session=None)
    assert await adapter._try_skill_turbo_resume(_ask_user_request(source), {}) is None


def test_hitl_placeholder_is_recognized_by_context_repair():
    rail = JiuSwarmStreamEventRail()
    tool_call_id = "call_982c"
    placeholders = {
        tool_call_id: rail._tool_interrupted_message("skill_acceleration_exec"),
    }
    names = {tool_call_id: "skill_acceleration_exec"}
    leaked = ToolMessage(
        content="{'success': False, 'error': '任务已暂停等待审批'}",
        tool_call_id=tool_call_id,
    )
    placeholder = ToolMessage(
        content=_SKILL_TURBO_HITL_PLACEHOLDER,
        tool_call_id=tool_call_id,
    )
    assert rail._is_tool_interrupt_placeholder(leaked, placeholders, names) is False
    assert rail._is_tool_interrupt_placeholder(placeholder, placeholders, names) is True


@pytest.mark.asyncio
async def test_skill_turbo_hitl_after_tool_call_writes_placeholder_tool_msg():
    rail = JiuSwarmStreamEventRail()
    rail.set_skill_turbo_adapter(object())
    session = _StreamSession()
    tool_call = SimpleNamespace(
        id="call_982c",
        name="skill_acceleration_exec",
        arguments={"query": "生成PPT"},
    )
    leaked = ToolMessage(
        content="{'success': False, 'error': '任务已暂停等待审批'}",
        tool_call_id="call_982c",
    )
    ctx = SimpleNamespace(
        session=session,
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name="skill_acceleration_exec",
            tool_args={"query": "生成PPT"},
            tool_result={"success": False, "error": "任务已暂停等待审批"},
            tool_msg=leaked,
        ),
        extra={},
        exception=None,
        request_force_finish=lambda *_args, **_kwargs: None,
    )
    inner_tc = SimpleNamespace(
        id="skill_turbo-tc-ask_user-1",
        name="ask_user",
        arguments={"questions": [{"question": "风格", "options": [{"label": "科技极简"}]}]},
    )
    tic = SimpleNamespace(
        request=SimpleNamespace(message="ask", tool_call_id="skill_turbo-tc-ask_user-1"),
        tool_call=inner_tc,
    )
    set_skill_turbo_hitl_tic(tic)
    try:
        await rail.after_tool_call(ctx)
    finally:
        set_skill_turbo_hitl_tic(None)

    assert isinstance(ctx.inputs.tool_msg, ToolMessage)
    assert ctx.inputs.tool_msg.content == rail._tool_interrupted_message(
        "skill_acceleration_exec"
    )
    assert ctx.inputs.tool_msg.tool_call_id == "call_982c"
    assert ctx.inputs.tool_msg is not leaked


class _ModelContext:
    def __init__(self, messages):
        self.messages = list(messages)

    def get_messages(self):
        return list(self.messages)

    def pop_messages(self, size):
        popped = self.messages[:size]
        self.messages = self.messages[size:]
        return popped

    async def add_messages(self, message):
        self.messages.append(message)


@pytest.mark.asyncio
async def test_fix_incomplete_tool_context_keeps_stop_hint_over_hitl_placeholder():
    rail = JiuSwarmStreamEventRail()
    tool_call_id = "call_982c"
    stop_hint_msg = ToolMessage(
        content="任务已完成" + _SKILL_TURBO_STOP_HINT,
        tool_call_id=tool_call_id,
    )
    ctx = SimpleNamespace(
        context=_ModelContext([
            UserMessage(content="生成一页PPT"),
            AssistantMessage(
                content="",
                tool_calls=[{
                    "type": "function",
                    "id": tool_call_id,
                    "function": {
                        "name": "skill_acceleration_exec",
                        "arguments": "{\"query\":\"生成PPT\"}",
                    },
                }],
            ),
            ToolMessage(
                content=_SKILL_TURBO_HITL_PLACEHOLDER,
                tool_call_id=tool_call_id,
            ),
            ToolMessage(
                content=_SKILL_TURBO_HITL_PLACEHOLDER,
                tool_call_id=tool_call_id,
            ),
            stop_hint_msg,
        ]),
        inputs=SimpleNamespace(tools=[]),
        session=None,
        extra={},
    )

    await rail._fix_incomplete_tool_context(ctx)

    messages = ctx.context.get_messages()
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == tool_call_id
    assert "任务已暂停等待审批" not in tool_msgs[0].content
    assert _SKILL_TURBO_HITL_PLACEHOLDER not in tool_msgs[0].content
    assert "The skill_acceleration_exec task is complete" in tool_msgs[0].content
    assert "skill_tool" in tool_msgs[0].content
