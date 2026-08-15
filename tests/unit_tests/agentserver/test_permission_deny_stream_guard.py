"""Stream/history guards for permission ASK interrupt and post-deny answers."""

from types import SimpleNamespace

import pytest

from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.harness.rails.interrupt.interrupt_base import InterruptResult

from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
    build_permission_deny_user_answer,
    extract_permission_deny_user_feedback,
    is_permission_deny_tool_result,
    is_premature_tool_hitl_stream_result,
    should_force_finish_on_permission_deny,
)
from jiuwenswarm.agents.harness.common.rails.stream_event_rail import (
    JiuSwarmStreamEventRail,
)


class _StreamSession:
    def __init__(self):
        self.chunks = []

    async def write_stream(self, chunk):
        self.chunks.append(chunk)


def _tool_ctx(
    session,
    *,
    tool_result=None,
    exception=None,
    interrupt_decision=None,
    force_finish_out=None,
):
    tool_call = SimpleNamespace(id="call-perm-1", name="write_file", arguments={})
    extra = {}
    if interrupt_decision is not None:
        extra["_interrupt_decision"] = interrupt_decision
    if force_finish_out is None:
        force_finish_out = []

    def _request_force_finish(payload):
        force_finish_out.append(payload)

    return SimpleNamespace(
        session=session,
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name="write_file",
            tool_args={},
            tool_result=tool_result,
        ),
        extra=extra,
        exception=exception,
        request_force_finish=_request_force_finish,
        force_finish_out=force_finish_out,
    )


class ToolInterruptException(Exception):
    def __init__(self):
        super().__init__()
        self.request = SimpleNamespace(tool_call_id="call-perm-1", message="")


def test_is_premature_tool_hitl_stream_result_matches_empty_failure_repr():
    assert is_premature_tool_hitl_stream_result(
        "success=False data=None error=''"
    )


def test_is_permission_deny_tool_result():
    denied = (
        "[PERMISSION_DENIED] User rejected the tool call. "
        "The operation was NOT performed. User feedback: nope"
    )
    assert is_permission_deny_tool_result(denied)
    assert not is_permission_deny_tool_result("Wrote 2 lines")


def test_build_permission_deny_user_answer_en():
    text = build_permission_deny_user_answer(
        "[PERMISSION_DENIED] User rejected the tool call. "
        "The operation was NOT performed. User feedback: dont do nothing",
        language="en",
    )
    assert "not performed" in text.lower()
    assert "dont do nothing" in text


@pytest.mark.asyncio
async def test_after_tool_call_skips_emit_on_tool_interrupt_exception():
    session = _StreamSession()
    ctx = _tool_ctx(
        session,
        tool_result="success=False data=None error=''",
        exception=ToolInterruptException(),
    )
    rail = JiuSwarmStreamEventRail()
    rail._conversation_ids["default"] = "conv-1"

    await rail.after_tool_call(ctx)

    assert session.chunks == []


@pytest.mark.asyncio
async def test_after_tool_call_skips_emit_on_interrupt_decision():
    session = _StreamSession()
    ctx = _tool_ctx(
        session,
        interrupt_decision=InterruptResult(
            request=InterruptRequest(tool_call_id="call-perm-1", value={"question": "approve?"}),
        ),
    )
    rail = JiuSwarmStreamEventRail()
    rail._conversation_ids["default"] = "conv-1"

    await rail.after_tool_call(ctx)

    assert session.chunks == []


def test_extract_permission_deny_user_feedback():
    denied = (
        "[PERMISSION_DENIED] User rejected the tool call. "
        "The operation was NOT performed. User feedback: write a calculator"
    )
    assert extract_permission_deny_user_feedback(denied) == "write a calculator"
    assert extract_permission_deny_user_feedback(
        "[PERMISSION_DENIED] User rejected the tool call. The operation was NOT performed."
    ) == ""


def test_should_force_finish_only_on_plain_deny():
    plain = "[PERMISSION_DENIED] User rejected the tool call. The operation was NOT performed."
    with_feedback = (
        "[PERMISSION_DENIED] User rejected the tool call. "
        "The operation was NOT performed. User feedback: use Read instead"
    )
    assert should_force_finish_on_permission_deny(plain) is True
    assert should_force_finish_on_permission_deny(with_feedback) is False


@pytest.mark.asyncio
async def test_after_tool_call_force_finishes_on_plain_permission_deny(monkeypatch):
    denied = "[PERMISSION_DENIED] User rejected the tool call. The operation was NOT performed."
    session = _StreamSession()
    force_finish_out = []
    ctx = _tool_ctx(session, tool_result=denied, force_finish_out=force_finish_out)
    rail = JiuSwarmStreamEventRail()
    rail._deep_agent = SimpleNamespace(
        system_prompt_builder=SimpleNamespace(language="cn"),
    )
    rail._conversation_ids["default"] = "conv-1"

    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: {"preferred_language": "en"},
    )

    await rail.after_tool_call(ctx)

    tool_results = [c for c in session.chunks if c.type == "tool_result"]
    assert len(tool_results) == 1
    assert force_finish_out
    assert "not performed" in force_finish_out[0]["output"].lower()
    assert force_finish_out[0]["result_type"] == "answer"


@pytest.mark.asyncio
async def test_after_tool_call_continues_when_permission_deny_has_user_feedback():
    denied = (
        "[PERMISSION_DENIED] User rejected the tool call. "
        "The operation was NOT performed. User feedback: write a calculator"
    )
    session = _StreamSession()
    force_finish_out = []
    ctx = _tool_ctx(session, tool_result=denied, force_finish_out=force_finish_out)
    rail = JiuSwarmStreamEventRail()
    rail._conversation_ids["default"] = "conv-1"

    await rail.after_tool_call(ctx)

    tool_results = [c for c in session.chunks if c.type == "tool_result"]
    assert len(tool_results) == 1
    assert force_finish_out == []
