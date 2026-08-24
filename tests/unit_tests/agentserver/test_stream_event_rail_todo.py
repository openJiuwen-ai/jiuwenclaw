from types import SimpleNamespace

import pytest
from openjiuwen.core.session.interaction.interaction import InteractionOutput
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.response import (
    InterruptRequest,
    ToolCallInterruptRequest,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs

from jiuwenswarm.agents.harness.common.rails.stream_event_rail import (
    JiuSwarmStreamEventRail,
)


class _FakeTodoTool:
    async def load_todos(self, session_id: str):
        assert session_id == "sess-1"
        return []


class _FakeSession:
    def __init__(self):
        self.outputs = []

    async def write_stream(self, output):
        self.outputs.append(output)


class _TestRail(JiuSwarmStreamEventRail):
    def install_todo_tool(self, tool):
        self._main_todo_tool = tool

    async def emit_todo_updated(self, session, session_id: str):
        await self._emit_todo_updated(session, session_id)

    async def emit_ask_user_question_if_interrupted(
        self,
        session,
        tool_call,
        tool_name,
        result,
        exception=None,
    ):
        await self._emit_ask_user_question_if_interrupted(
            session,
            tool_call,
            tool_name,
            result,
            exception,
        )

    async def emit_context_usage(self, ctx):
        await self._emit_context_usage(ctx)


@pytest.mark.asyncio
async def test_empty_todo_list_is_emitted_to_clear_frontend():
    rail = _TestRail()
    rail.install_todo_tool(_FakeTodoTool())
    session = _FakeSession()

    await rail.emit_todo_updated(session, "sess-1")

    assert len(session.outputs) == 1
    output = session.outputs[0]
    assert output.type == "todo.updated"
    assert output.payload == {"todos": []}


@pytest.mark.asyncio
async def test_context_usage_reports_input_tokens_instead_of_reply_total(monkeypatch):
    class _UsageMetadata:
        @staticmethod
        def model_dump():
            return {
                "input_tokens": 1200,
                "output_tokens": 800,
                "total_tokens": 2000,
            }

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.rails.stream_event_rail.ContextUtils.resolve_context_max",
        lambda **_kwargs: 10000,
    )
    session = _FakeSession()
    ctx = SimpleNamespace(
        session=session,
        context=SimpleNamespace(),
        agent=None,
        inputs=SimpleNamespace(
            response=SimpleNamespace(usage_metadata=_UsageMetadata()),
        ),
    )

    await _TestRail().emit_context_usage(ctx)

    assert len(session.outputs) == 1
    output = session.outputs[0]
    assert output.type == "context.usage"
    assert output.payload == {
        "rate": 12.0,
        "context_max": 10000,
        "tokens_used": 1200,
    }


@pytest.mark.asyncio
async def test_context_usage_keeps_zero_input_tokens_instead_of_falling_back(monkeypatch):
    class _UsageMetadata:
        @staticmethod
        def model_dump():
            return {
                "input_tokens": 0,
                "total_tokens": 800,
            }

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.rails.stream_event_rail.ContextUtils.resolve_context_max",
        lambda **_kwargs: 10000,
    )
    session = _FakeSession()
    ctx = SimpleNamespace(
        session=session,
        context=SimpleNamespace(),
        agent=None,
        inputs=SimpleNamespace(
            response=SimpleNamespace(usage_metadata=_UsageMetadata()),
        ),
    )

    await _TestRail().emit_context_usage(ctx)

    assert session.outputs[0].payload["tokens_used"] == 0


@pytest.mark.asyncio
async def test_context_usage_keeps_runtime_context_limit_fallback(monkeypatch):
    captured_kwargs = {}

    def _resolve_context_max(**kwargs):
        captured_kwargs.update(kwargs)
        return 1000000

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.rails.stream_event_rail.ContextUtils.resolve_context_max",
        _resolve_context_max,
    )
    session = _FakeSession()
    ctx = SimpleNamespace(
        session=session,
        context=SimpleNamespace(_context_window_tokens=1048576),
        agent=None,
        inputs=SimpleNamespace(response=None),
    )

    await _TestRail().emit_context_usage(ctx)

    assert captured_kwargs["fallback_context_window_tokens"] == 1048576
    assert session.outputs[0].payload["context_max"] == 1000000


@pytest.mark.asyncio
async def test_ask_user_interrupt_emits_question_event_from_tool_args():
    class ToolInterruptException(Exception):
        def __init__(self):
            super().__init__()
            self.request = SimpleNamespace(
                tool_call_id="tool-ask-1",
                tool_args={
                    "questions": [
                        {
                            "question": "请选择方案",
                            "header": "方案",
                            "options": [
                                {"label": "A", "description": "方案 A"},
                            ],
                        }
                    ]
                },
            )

    session = _FakeSession()
    tool_call = SimpleNamespace(id="tool-ask-1", arguments="{}")
    rail = _TestRail()

    await rail.emit_ask_user_question_if_interrupted(
        session,
        tool_call,
        "ask_user",
        ToolInterruptException(),
    )

    assert len(session.outputs) == 1
    output = session.outputs[0]
    assert output.type == "chat.ask_user_question"
    assert output.payload["request_id"] == "tool-ask-1"
    assert output.payload["source"] == "ask_user_interrupt"
    assert output.payload["questions"][0]["question"] == "请选择方案"


@pytest.mark.asyncio
async def test_ask_user_interrupt_emits_question_event_from_exception_cause():
    class ToolInterruptException(Exception):
        def __init__(self):
            super().__init__()
            self.request = SimpleNamespace(
                tool_call_id="tool-ask-2",
                questions=[
                    {
                        "question": "是否继续",
                        "header": "确认",
                        "options": [
                            {"label": "继续", "description": "继续执行"},
                        ],
                    }
                ],
            )

    session = _FakeSession()
    tool_call = SimpleNamespace(id="tool-ask-2", arguments="{}")
    exception = SimpleNamespace(cause=ToolInterruptException())
    rail = _TestRail()

    await rail.emit_ask_user_question_if_interrupted(
        session,
        tool_call,
        "ask_user",
        None,
        exception,
    )

    assert len(session.outputs) == 1
    output = session.outputs[0]
    assert output.type == "chat.ask_user_question"
    assert output.payload["request_id"] == "tool-ask-2"
    assert output.payload["questions"][0]["question"] == "是否继续"


@pytest.mark.asyncio
async def test_permission_interrupt_for_read_file_emits_approval_question():
    class ToolInterruptException(Exception):
        def __init__(self):
            super().__init__()
            self.request = SimpleNamespace(
                tool_call_id="tool-read-1",
                tool_name="read_file",
                tool_args={"file_path": r"C:\Users\Administrator\Desktop\分析.md"},
                message="**工具 `read_file` 需要授权才能执行**\n\n请确认是否允许该操作。",
            )

    session = _FakeSession()
    tool_call = SimpleNamespace(
        id="tool-read-1",
        name="read_file",
        arguments={"file_path": r"C:\Users\Administrator\Desktop\分析.md"},
    )
    exception = SimpleNamespace(cause=ToolInterruptException())

    await _TestRail().emit_ask_user_question_if_interrupted(
        session,
        tool_call,
        "read_file",
        None,
        exception,
    )

    assert len(session.outputs) == 1
    output = session.outputs[0]
    assert output.type == "chat.ask_user_question"
    assert output.payload["request_id"] == "tool-read-1"
    assert output.payload["source"] == "permission_interrupt"
    assert output.payload["questions"][0]["header"] == "权限审批: read_file"


@pytest.mark.asyncio
async def test_before_tool_permission_interrupt_emits_question_on_tool_exception_once():
    """A BEFORE_TOOL_CALL interrupt must publish HITL before AFTER_TOOL_CALL runs."""
    session = _FakeSession()
    tool_call = SimpleNamespace(
        id="tool-bash-1",
        name="bash",
        arguments={"command": "curl https://www.google.com"},
    )
    interrupt = ToolInterruptException(
        request=InterruptRequest(
            message="**工具 `bash` 需要授权才能执行**\n\n请确认是否允许该操作。",
        ),
        tool_call=tool_call,
    )
    ctx = AgentCallbackContext(
        agent=None,
        session=session,
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name="bash",
            tool_args=tool_call.arguments,
        ),
        exception=interrupt,
    )
    rail = _TestRail()

    await rail.on_tool_exception(ctx)

    questions = [output for output in session.outputs if output.type == "chat.ask_user_question"]
    assert len(questions) == 1
    assert questions[0].payload["request_id"] == "tool-bash-1"
    assert questions[0].payload["source"] == "permission_interrupt"
    assert questions[0].payload["questions"][0]["header"] == "权限审批: bash"

    await rail.after_tool_call(ctx)

    questions = [output for output in session.outputs if output.type == "chat.ask_user_question"]
    assert len(questions) == 1
    assert not [output for output in session.outputs if output.type == "tool_result"]


@pytest.mark.asyncio
async def test_subagent_interrupt_result_is_not_emitted_as_tool_result():
    session = _FakeSession()
    tool_call = SimpleNamespace(
        id="tool-task-1",
        name="task_tool",
        arguments={"subagent_type": "general-purpose"},
    )
    ctx = AgentCallbackContext(
        agent=None,
        session=session,
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name="task_tool",
            tool_args=tool_call.arguments,
            tool_result={
                "result_type": "interrupt",
                "interrupt_ids": ["inner-call"],
                "state": [
                    OutputSchema(
                        type="__interaction__",
                        index=0,
                        payload=InteractionOutput(
                            id="inner-call",
                            value=ToolCallInterruptRequest(
                                message="**工具 `write_file` 需要授权才能执行**",
                                tool_name="write_file",
                                tool_call_id="inner-call",
                                tool_args={"file_path": ".env.approval-test"},
                            ),
                        ),
                    ),
                ],
            },
        ),
    )

    await _TestRail().after_tool_call(ctx)

    assert not [output for output in session.outputs if output.type == "tool_result"]
    questions = [output for output in session.outputs if output.type == "chat.ask_user_question"]
    assert len(questions) == 1
    assert questions[0].payload["request_id"] == "inner-call"
    assert questions[0].payload["source"] == "permission_interrupt"


@pytest.mark.asyncio
async def test_task_tool_resume_does_not_reemit_tool_start_events():
    session = _FakeSession()
    approval_input = InteractiveInput()
    approval_input.update("inner-call", {"approved": True})
    tool_call = SimpleNamespace(
        id="tool-task-1",
        name="task_tool",
        arguments={
            "subagent_type": "general-purpose",
            "query": approval_input,
        },
    )
    ctx = AgentCallbackContext(
        agent=None,
        session=session,
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name="task_tool",
            tool_args=tool_call.arguments,
        ),
    )

    await _TestRail().before_tool_call(ctx)

    assert not [
        output
        for output in session.outputs
        if output.type in {"tool_call", "tool_update"}
    ]


@pytest.mark.asyncio
async def test_task_tool_first_call_still_emits_tool_start_events():
    session = _FakeSession()
    tool_call = SimpleNamespace(
        id="tool-task-1",
        name="task_tool",
        arguments={
            "subagent_type": "general-purpose",
            "query": "检查文件",
        },
    )
    ctx = AgentCallbackContext(
        agent=None,
        session=session,
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name="task_tool",
            tool_args=tool_call.arguments,
        ),
    )

    await _TestRail().before_tool_call(ctx)

    assert [output.type for output in session.outputs] == [
        "tool_call",
        "tool_update",
    ]


@pytest.mark.asyncio
async def test_interrupt_enrichment_preserves_metadata_and_ui_options():
    session = _FakeSession()
    tool_call = SimpleNamespace(
        id="tool-structured-1",
        name="custom_review_tool",
        arguments={"target": "demo"},
    )
    interrupt = ToolInterruptException(
        request=InterruptRequest(
            message="Review this structured operation",
            ui_options=[
                {
                    "label": "Approve once",
                    "value": "allow_once",
                    "description": "Approve only this operation",
                }
            ],
            metadata={
                "source": "evolution_interrupt",
                "approval_kind": "simplify",
            },
        ),
        tool_call=tool_call,
    )

    await _TestRail().emit_ask_user_question_if_interrupted(
        session,
        tool_call,
        tool_call.name,
        interrupt,
    )

    output = session.outputs[0]
    assert output.payload["source"] == "evolution_interrupt"
    assert output.payload["approval_kind"] == "simplify"
    assert output.payload["questions"][0]["options"][0] == {
        "label": "Approve once",
        "value": "allow_once",
        "description": "Approve only this operation",
    }


@pytest.mark.asyncio
async def test_interrupt_enrichment_preserves_dict_backed_message():
    class ToolInterruptException(Exception):
        def __init__(self, request, tool_call):
            self.request = request
            self.tool_call = tool_call
            super().__init__(str(request.get("message") or ""))

    session = _FakeSession()
    tool_call = SimpleNamespace(
        id="tool-plan-1",
        name="exit_plan_mode",
        arguments={"plan": "demo"},
    )
    interrupt = ToolInterruptException(
        request={
            "message": "Review the exact plan before execution",
            "payload_schema": {"type": "object"},
        },
        tool_call=tool_call,
    )

    await _TestRail().emit_ask_user_question_if_interrupted(
        session,
        tool_call,
        tool_call.name,
        interrupt,
    )

    output = session.outputs[0]
    assert output.payload["source"] == "confirm_interrupt"
    assert output.payload["questions"][0]["question"] == (
        "Review the exact plan before execution"
    )
