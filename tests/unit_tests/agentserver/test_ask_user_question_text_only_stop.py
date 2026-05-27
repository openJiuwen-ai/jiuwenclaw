# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ask_user_question text_only turn-stop helpers."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from jiuwenclaw.agentserver.deep_agent.rails.ask_user_question_resume_rail import (
    AskUserQuestionResumeRail,
)
from jiuwenclaw.agentserver.deep_agent.rails.stream_event_rail import JiuClawStreamEventRail
from jiuwenclaw.agentserver.deep_agent import interface_deep as interface_deep_module
from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
from jiuwenclaw.agentserver.tools.ask_user_question_session_state import (
    SESSION_AWAITING_TEXT_ONLY_ASK_KEY,
    clear_pending_follow_ups,
    consume_session_awaiting_text_only_ask_reply,
    mark_session_awaiting_text_only_ask_reply,
    pop_text_only_resume_user_query,
    session_awaiting_text_only_ask_reply,
    store_text_only_resume_user_query,
)
from jiuwenclaw.agentserver.tools.ask_user_question_turn_stop import (
    ASK_USER_QUESTION_TOOL_NAMES,
    extract_text_only_stop_payload,
    is_ask_user_question_tool_name,
)
from jiuwenclaw.schema.agent import AgentRequest
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs


@pytest.mark.parametrize("name", sorted(ASK_USER_QUESTION_TOOL_NAMES))
def test_is_ask_user_question_tool_name_recognizes_registered_names(name: str) -> None:
    assert is_ask_user_question_tool_name(name) is True


def test_is_ask_user_question_tool_name_rejects_other_tools() -> None:
    assert is_ask_user_question_tool_name("read_file") is False


def test_extract_text_only_stop_payload_from_dict() -> None:
    payload = {
        "status": "text_only",
        "formatted_questions": "## 需要您的确认\n\n**风格**",
        "message": "wait",
        "answers": [],
        "stop_agent_turn": True,
    }
    stop = extract_text_only_stop_payload(payload)
    assert stop is not None
    assert stop["formatted_questions"].startswith("## 需要您的确认")
    assert stop["status"] == "text_only"


def test_extract_text_only_stop_payload_from_json_string() -> None:
    inner = {
        "status": "text_only",
        "formatted_questions": "请选择风格",
    }
    stop = extract_text_only_stop_payload(json.dumps(inner, ensure_ascii=False))
    assert stop is not None
    assert stop["formatted_questions"] == "请选择风格"


def test_extract_text_only_stop_payload_ignores_answered_status() -> None:
    assert extract_text_only_stop_payload({"status": "answered", "answers": [{}]}) is None


def test_extract_text_only_stop_payload_requires_non_empty_formatted_questions() -> None:
    assert extract_text_only_stop_payload({"status": "text_only", "formatted_questions": ""}) is None


@pytest.mark.asyncio
async def test_text_only_force_finish_interrupts_outer_task_loop() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.streams = []

        async def write_stream(self, schema) -> None:
            self.streams.append(schema)

    session = FakeSession()
    tool_call = SimpleNamespace(
        id="call-ask",
        name="ask_user_question",
        arguments={},
    )
    ctx = AgentCallbackContext(
        agent=object(),
        session=session,
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name="ask_user_question",
            tool_result=json.dumps(
                {
                    "status": "text_only",
                    "formatted_questions": "## 需要您的确认\n\n请选择风格",
                    "stop_agent_turn": True,
                },
                ensure_ascii=False,
            ),
        ),
    )

    await JiuClawStreamEventRail().after_tool_call(ctx)

    finish = ctx.consume_force_finish()
    assert finish is not None
    assert finish.result["result_type"] == "interrupt"
    assert finish.result["ask_user_question"]["awaiting_user_reply"] is True
    assert [schema.type for schema in session.streams] == ["llm_output", "tool_result"]
    assert session.streams[0].payload["content"].startswith("## 需要您的确认")


@pytest.mark.asyncio
async def test_text_only_pause_stops_stream_before_followup_llm_output() -> None:
    yielded_after_pause = False

    async def fake_stream(_agent, _inputs):
        nonlocal yielded_after_pause
        yield SimpleNamespace(
            type="tool_result",
            payload={
                "tool_result": {
                    "tool_name": "ask_user_question",
                    "result": json.dumps(
                        {
                            "status": "text_only",
                            "formatted_questions": "## 需要您的确认\n\n请选择风格",
                            "stop_agent_turn": True,
                        },
                        ensure_ascii=False,
                    ),
                },
            },
        )
        yielded_after_pause = True
        yield SimpleNamespace(type="llm_output", payload={"content": "不应该继续生成"})

    adapter = JiuWenClawDeepAdapter()
    adapter._instance = object()
    adapter._model = SimpleNamespace(model_config=SimpleNamespace(model_name="test-model"))
    adapter._telemetry_rail = None

    request = AgentRequest(
        request_id="req-text-only-stop",
        channel_id="officeclaw",
        session_id="sess-text-only-stop",
        params={"query": "生成PPT", "mode": "agent.plan"},
    )

    with (
        patch.object(adapter, "_has_valid_model_config", return_value=True),
        patch.object(adapter, "_handle_slash_command", AsyncMock(return_value=None)),
        patch.object(adapter, "_plain_chat_should_clear_stale_interrupt", return_value=False),
        patch.object(adapter, "_resolve_model_for_request", return_value=adapter._model),
        patch.object(adapter, "_apply_model_to_react_agent", return_value=None),
        patch.object(adapter, "_update_runtime_config", AsyncMock()),
        patch.object(adapter, "_reset_runtime_cron_context", return_value=None),
        patch.object(adapter, "_untrack_session_toolkit", return_value=None),
        patch.object(interface_deep_module.Runner, "run_agent_streaming", side_effect=fake_stream),
    ):
        chunks = [
            chunk
            async for chunk in adapter.process_message_stream_impl(
                request,
                {
                    "query": request.params["query"],
                    "conversation_id": request.session_id,
                    "request_id": request.request_id,
                },
            )
        ]

    payloads = [chunk.payload for chunk in chunks]
    assert yielded_after_pause is True
    assert any(payload and payload.get("event_type") == "chat.tool_result" for payload in payloads)
    assert not any(payload and payload.get("content") == "不应该继续生成" for payload in payloads)
    assert payloads[-1] == {"event_type": "chat.invocation_paused", "awaiting_user_input": True}
    assert chunks[-1].is_complete is True


def test_session_awaiting_text_only_ask_reply_round_trip() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self._state: dict[str, object] = {}

        def get_state(self, key: str):
            return self._state.get(key)

        def update_state(self, patch: dict[str, object]) -> None:
            self._state.update(patch)

    session = FakeSession()
    assert session_awaiting_text_only_ask_reply(session) is False
    mark_session_awaiting_text_only_ask_reply(session)
    assert session.get_state(SESSION_AWAITING_TEXT_ONLY_ASK_KEY) is True
    assert consume_session_awaiting_text_only_ask_reply(session) is True
    assert session_awaiting_text_only_ask_reply(session) is False
    assert consume_session_awaiting_text_only_ask_reply(session) is False


def test_clear_pending_follow_ups_only_clears_follow_up_queue() -> None:
    class FakeState:
        def __init__(self) -> None:
            self.pending_follow_ups = ["Stage 3: 内容策划"]
            self.task_plan = object()

    class FakeDeepAgent:
        def __init__(self) -> None:
            self.state = FakeState()

        def load_state(self, _session):
            return self.state

        def save_state(self, _session, state):
            self.state = state

    deep_agent = FakeDeepAgent()
    cleared = clear_pending_follow_ups(deep_agent, object())
    assert cleared == ["Stage 3: 内容策划"]
    assert deep_agent.state.pending_follow_ups == []
    assert deep_agent.state.task_plan is not None


@pytest.mark.asyncio
async def test_text_only_pause_marks_session_for_resume() -> None:
    async def fake_stream(_agent, _inputs):
        yield SimpleNamespace(
            type="tool_result",
            payload={
                "tool_result": {
                    "tool_name": "ask_user_question",
                    "result": json.dumps(
                        {
                            "status": "text_only",
                            "formatted_questions": "## 需要您的确认\n\n请选择风格",
                            "stop_agent_turn": True,
                        },
                        ensure_ascii=False,
                    ),
                },
            },
        )

    adapter = JiuWenClawDeepAdapter()
    adapter._instance = object()
    adapter._model = SimpleNamespace(model_config=SimpleNamespace(model_name="test-model"))
    adapter._telemetry_rail = None
    mark_mock = AsyncMock()

    request = AgentRequest(
        request_id="req-text-only-mark",
        channel_id="officeclaw",
        session_id="sess-text-only-mark",
        params={"query": "生成PPT", "mode": "agent.plan"},
    )

    with (
        patch.object(adapter, "_has_valid_model_config", return_value=True),
        patch.object(adapter, "_handle_slash_command", AsyncMock(return_value=None)),
        patch.object(adapter, "_plain_chat_should_clear_stale_interrupt", return_value=False),
        patch.object(adapter, "_resolve_model_for_request", return_value=adapter._model),
        patch.object(adapter, "_apply_model_to_react_agent", return_value=None),
        patch.object(adapter, "_update_runtime_config", AsyncMock()),
        patch.object(adapter, "_reset_runtime_cron_context", return_value=None),
        patch.object(adapter, "_untrack_session_toolkit", return_value=None),
        patch.object(adapter, "_mark_session_awaiting_text_only_ask_reply", mark_mock),
        patch.object(interface_deep_module.Runner, "run_agent_streaming", side_effect=fake_stream),
    ):
        chunks = [
            chunk
            async for chunk in adapter.process_message_stream_impl(
                request,
                {
                    "query": request.params["query"],
                    "conversation_id": request.session_id,
                    "request_id": request.request_id,
                },
            )
        ]

    mark_mock.assert_awaited_once_with("sess-text-only-mark")
    assert chunks[-1].payload == {"event_type": "chat.invocation_paused", "awaiting_user_input": True}


@pytest.mark.asyncio
async def test_plain_reply_prepares_text_only_resume() -> None:
    async def fake_stream(_agent, _inputs):
        if False:
            yield SimpleNamespace()

    adapter = JiuWenClawDeepAdapter()
    adapter._instance = object()
    adapter._model = SimpleNamespace(model_config=SimpleNamespace(model_name="test-model"))
    adapter._telemetry_rail = None
    prepare_mock = AsyncMock()

    request = AgentRequest(
        request_id="req-text-only-resume",
        channel_id="officeclaw",
        session_id="sess-text-only-resume",
        params={"query": "3-6 页，普通大众，景点介绍", "mode": "agent.plan"},
    )

    with (
        patch.object(adapter, "_plain_chat_should_clear_stale_interrupt", return_value=True),
        patch.object(adapter, "_clear_session_persisted_interrupt_state", AsyncMock()),
        patch.object(adapter, "_prepare_text_only_ask_user_resume", prepare_mock),
        patch.object(adapter, "_has_valid_model_config", return_value=True),
        patch.object(adapter, "_handle_slash_command", AsyncMock(return_value=None)),
        patch.object(adapter, "_resolve_model_for_request", return_value=adapter._model),
        patch.object(adapter, "_apply_model_to_react_agent", return_value=None),
        patch.object(adapter, "_update_runtime_config", AsyncMock()),
        patch.object(adapter, "_reset_runtime_cron_context", return_value=None),
        patch.object(adapter, "_untrack_session_toolkit", return_value=None),
        patch.object(interface_deep_module.Runner, "run_agent_streaming", side_effect=fake_stream),
    ):
        _ = [
            chunk
            async for chunk in adapter.process_message_stream_impl(
                request,
                {"query": request.params["query"], "conversation_id": request.session_id},
            )
        ]

    prepare_mock.assert_awaited_once_with("sess-text-only-resume", "3-6 页，普通大众，景点介绍")


@pytest.mark.asyncio
async def test_resume_rail_restores_user_query_before_task_iteration() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self._state: dict[str, object] = {}

        def get_session_id(self) -> str:
            return "sess-resume-rail"

        def get_state(self, key: str):
            return self._state.get(key)

        def update_state(self, patch: dict[str, object]) -> None:
            self._state.update(patch)

    session = FakeSession()
    store_text_only_resume_user_query(session, "3-6 页，普通大众，景点介绍")

    from openjiuwen.core.single_agent.rail.base import TaskIterationInputs

    ctx = AgentCallbackContext(
        agent=object(),
        session=session,
        inputs=TaskIterationInputs(
            iteration=1,
            loop_event=None,
            conversation_id="sess-resume-rail",
            query="Stage 3: 内容策划(Alice-1)",
            is_follow_up=False,
        ),
    )

    await AskUserQuestionResumeRail().before_task_iteration(ctx)

    assert ctx.inputs.query == "3-6 页，普通大众，景点介绍"
    assert ctx.inputs.is_follow_up is True
    assert pop_text_only_resume_user_query(session) is None

