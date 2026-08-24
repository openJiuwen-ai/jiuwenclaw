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
async def test_tool_result_emitted_for_executed_tool_call() -> None:
    session = _FakeSession()
    ctx = _ctx(session)

    await JiuSwarmStreamEventRail().after_tool_call(ctx)

    assert [output.type for output in session.outputs] == ["tool_result"]


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
