from types import SimpleNamespace

import pytest

from openjiuwen.core.single_agent.rail.base import ToolCallInputs

from jiuwenswarm.agents.harness.common.rails.stream_event_rail import (
    JiuSwarmStreamEventRail,
)
from jiuwenswarm.agents.harness.common.tools.symphony_status_events import (
    emit_symphony_status,
)


class _StreamSession:
    def __init__(self):
        self.chunks = []

    async def write_stream(self, chunk):
        self.chunks.append(chunk)


def _ctx(
    session,
    tool_name: str,
    tool_call_id: str = "call-1",
    tool_result=None,
):
    tool_call = SimpleNamespace(id=tool_call_id, name=tool_name, arguments={})
    return SimpleNamespace(
        session=session,
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name=tool_name,
            tool_args={},
            tool_result=tool_result if tool_result is not None else {"success": True},
        ),
        extra={},
        exception=None,
    )


@pytest.mark.asyncio
async def test_stream_event_rail_enables_symphony_status_events_for_plan_tool():
    rail = JiuSwarmStreamEventRail()
    session = _StreamSession()
    ctx = _ctx(session, "symphony_compose_score", tool_call_id="parent-call")

    await rail.before_tool_call(ctx)
    await emit_symphony_status("checking_score", "正在读取 Symphony 总谱...")

    status_events = [
        chunk
        for chunk in session.chunks
        if chunk.type == "chat.symphony_status"
    ]
    assert len(status_events) == 1
    assert status_events[0].payload["operation_id"] == "parent-call"
    assert status_events[0].payload["phase"] == "checking_score"

    await rail.after_tool_call(ctx)
    chunk_count_after_cleanup = len(session.chunks)
    await emit_symphony_status("planning", "正在编排技能执行乐谱...")

    assert len(session.chunks) == chunk_count_after_cleanup


@pytest.mark.asyncio
async def test_stream_event_rail_directly_displays_symphony_compose_score_result():
    rail = JiuSwarmStreamEventRail()
    session = _StreamSession()
    result = {
        "success": True,
        "direct_display": True,
        "display_format": "markdown",
        "content": "## Symphony plan\n\n```mermaid\nflowchart LR\n  A --> B\n```",
        "mermaid": "flowchart LR\n  A --> B",
        "score_status": {"success": True, "exists": True, "stale": False},
    }
    ctx = _ctx(session, "symphony_compose_score", tool_result=result)

    await rail.before_tool_call(ctx)
    await rail.after_tool_call(ctx)

    tool_results = []
    for chunk in session.chunks:
        tool_result = chunk.payload.get("tool_result")
        if (
            chunk.type == "tool_result"
            and tool_result is not None
            and tool_result.get("tool_name") == "symphony_compose_score"
        ):
            tool_results.append(tool_result)
    assert tool_results[0]["raw_output"] == result
    assert tool_results[0]["score_status"] == result["score_status"]
    assert tool_results[0]["direct_display"] is True
    direct_messages = [chunk for chunk in session.chunks if chunk.type == "chat.final"]
    assert len(direct_messages) == 1
    assert direct_messages[0].payload["content"] == result["content"]
    assert direct_messages[0].payload["mermaid"] == result["mermaid"]
    assert direct_messages[0].payload["score_status"] == result["score_status"]


@pytest.mark.asyncio
async def test_stream_event_rail_does_not_enable_symphony_status_events_for_other_tools():
    rail = JiuSwarmStreamEventRail()
    session = _StreamSession()
    ctx = _ctx(session, "todo_list")

    await rail.before_tool_call(ctx)
    chunk_count_after_top_level_call = len(session.chunks)
    await emit_symphony_status("checking_score", "正在读取 Symphony 总谱...")

    assert len(session.chunks) == chunk_count_after_top_level_call
