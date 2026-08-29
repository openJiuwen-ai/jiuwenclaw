# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tool-result streaming behaviour of JiuSwarmStreamEventRail."""

# pylint: disable=protected-access

from types import SimpleNamespace

import pytest
from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.rail.base import ToolCallInputs

from jiuwenswarm.agents.harness.common.rails.stream_event_rail import (
    JiuSwarmStreamEventRail,
    extract_tool_interrupt,
)


class _FakeSession:
    def __init__(self):
        self.outputs = []

    async def write_stream(self, output):
        self.outputs.append(output)

    @staticmethod
    def get_session_id() -> str:
        return "sess-1"


def _pending_approval_exception() -> AbortError:
    """Mirror what the approval rail raises out of ``before_tool_call``."""
    return AbortError(
        "Tool execution interrupted: exit_plan_mode",
        cause=ToolInterruptException(request=InterruptRequest(message="计划审批")),
    )


def _ctx(session: _FakeSession, *, exception: object = None):
    tool_call = SimpleNamespace(id="call_1", name="exit_plan_mode", arguments={})
    return SimpleNamespace(
        session=session,
        agent=None,
        context=None,
        extra={},
        exception=exception,
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name="exit_plan_mode",
            tool_args={},
            tool_result=SimpleNamespace(success=False, data=None, error=""),
        ),
    )


@pytest.mark.asyncio
async def test_no_tool_result_emitted_while_waiting_for_approval() -> None:
    """A call suspended for approval has no result to report yet."""
    session = _FakeSession()
    ctx = _ctx(session, exception=_pending_approval_exception())

    await JiuSwarmStreamEventRail().after_tool_call(ctx)

    assert [output.type for output in session.outputs] == []


@pytest.mark.asyncio
async def test_skill_turbo_hitl_emits_ask_user_question_from_inner_tool_call() -> None:
    """Nested ask_user inside skill_acceleration_exec must still emit the question card."""
    from jiuwenswarm.server.runtime.skill_turbo import skill_turbo_tools as st_tools

    session = _FakeSession()
    outer_tc = SimpleNamespace(
        id="skill_turbo-outer",
        name="skill_acceleration_exec",
        arguments={"query": "做PPT"},
    )
    inner_tc = SimpleNamespace(
        id="skill_turbo-tc-ask_user-page",
        name="ask_user",
        arguments={
            "questions": [
                {
                    "header": "页数",
                    "question": "需要多少页内容页？（不含封面、结束页）",
                    "options": [{"label": "3-6 页（推荐）"}],
                }
            ]
        },
    )
    inner_tic = ToolInterruptException(
        request=InterruptRequest(message="ask_user"),
        tool_call=inner_tc,
    )
    token = st_tools.set_skill_turbo_hitl_tic(inner_tic)
    rail = JiuSwarmStreamEventRail()
    rail.set_skill_turbo_adapter(object())
    ctx = SimpleNamespace(
        session=session,
        agent=None,
        context=None,
        extra={},
        exception=None,
        inputs=ToolCallInputs(
            tool_call=outer_tc,
            tool_name="skill_acceleration_exec",
            tool_args={"query": "做PPT"},
            tool_result={"success": False, "error": "任务已暂停等待审批"},
        ),
    )
    try:
        await rail.after_tool_call(ctx)
    finally:
        st_tools._skill_turbo_hitl_tic.reset(token)

    assert any(getattr(o, "type", None) == "chat.ask_user_question" for o in session.outputs)
    assert not any(getattr(o, "type", None) == "tool_result" for o in session.outputs)
    assert extract_tool_interrupt(ctx.inputs.tool_result) is not None
    ask_out = next(o for o in session.outputs if o.type == "chat.ask_user_question")
    payload = ask_out.payload if isinstance(ask_out.payload, dict) else {}
    questions = payload.get("questions") or []
    assert questions and questions[0].get("header") == "页数"


def test_shared_interrupt_unwrap_handles_deep_and_cyclic_chains() -> None:
    interrupt = ToolInterruptException(request=InterruptRequest(message="approve"))
    wrapped = interrupt
    for index in range(10):
        wrapped = SimpleNamespace(cause=wrapped, marker=index)

    assert extract_tool_interrupt(wrapped) is interrupt

    first = SimpleNamespace(cause=None)
    second = SimpleNamespace(cause=first)
    first.cause = second
    assert extract_tool_interrupt(first) is None
