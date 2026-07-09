"""MessageHandler unit tests."""

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest

from jiuwenavatar.common.schema import Message
from jiuwenavatar.common.schema.message import ReqMethod
from jiuwenavatar.gateway.message_handler.message_handler import MessageHandler


class _FakeAgentClient:
    sent_requests: list[object] = []
    response_payload: dict[str, object] = {
        "event_type": "chat.interrupt_result",
        "message": "当前没有可取消的团队任务",
        "success": False,
    }

    @staticmethod
    async def send_request(env: object) -> SimpleNamespace:
        _FakeAgentClient.sent_requests.append(env)
        return SimpleNamespace(
            request_id="interrupt-1",
            channel_id="feishu_enterprise",
            ok=True,
            payload=dict(_FakeAgentClient.response_payload),
            metadata=None,
        )

    @staticmethod
    async def send_request_stream(env: object) -> AsyncIterator[object]:
        if False:
            yield env


class _TestMessageHandler(MessageHandler):
    @classmethod
    def create(cls) -> "_TestMessageHandler":
        setattr(MessageHandler, "_instance", None)
        setattr(cls, "_instance", None)
        _FakeAgentClient.sent_requests = []
        return cls(_FakeAgentClient())

    def seed_pending_evolution_approval(
        self,
        session_id: str,
        request_id: str,
    ) -> None:
        marker = getattr(self, "_mark_pending_evolution_approval")
        marker(session_id, request_id)

    def seed_session_evolution_in_progress(self, session_id: str) -> None:
        marker = getattr(self, "_mark_session_evolution_in_progress")
        marker(session_id)

    def seed_queued_supplement_input(
        self,
        session_id: str,
        payload: dict[str, object],
    ) -> None:
        queued_inputs = getattr(self, "_queued_supplement_input")
        queued_inputs[session_id] = payload

    async def handle_evolution_chunk(
        self,
        chunk: SimpleNamespace,
        session_id: str,
        request_metadata: dict[str, object] | None = None,
    ) -> None:
        handler = getattr(self, "_handle_evolution_chunk")
        await handler(chunk, session_id, request_metadata)

    def finish_evolution_approval_if_current(
        self,
        session_id: str,
        answered_request_id: str,
    ) -> dict[str, object] | None:
        finisher = getattr(self, "_finish_evolution_approval_if_current")
        return finisher(session_id, answered_request_id)

    def pending_evolution_approval(self, session_id: str) -> str | None:
        approvals = getattr(self, "_pending_evolution_approval")
        return approvals.get(session_id)

    def has_session_evolution_in_progress(self, session_id: str) -> bool:
        checker = getattr(self, "_is_session_evolution_in_progress")
        return checker(session_id)

    def queued_supplement_input(self, session_id: str) -> dict[str, object] | None:
        queued_inputs = getattr(self, "_queued_supplement_input")
        return queued_inputs.get(session_id)

    def pop_user_message_nowait(self):
        user_messages = getattr(self, "_user_messages")
        return user_messages.get_nowait()

    def should_emit_processing_status_for_stream(self, msg: Message) -> bool:
        return self._should_emit_processing_status_for_stream(msg)

    async def cancel_agent_work_for_session(
        self,
        msg: Message,
        old_sid: str | None,
        *,
        publish_interrupt_result: bool = True,
    ) -> None:
        await self._cancel_agent_work_for_session(
            msg,
            old_sid,
            publish_interrupt_result=publish_interrupt_result,
        )

    def build_queued_chat_send_message(
        self,
        msg: Message,
        new_input: str,
        original_request: str = "",
    ) -> Message:
        return self._build_queued_chat_send_message(
            msg,
            new_input,
            original_request=original_request,
        )

    def remember_user_query_context(self, msg: Message) -> None:
        self._remember_user_query_context(msg)

    def get_session_last_user_query(self, session_id: str) -> str:
        return self._get_session_last_user_query(session_id)


def _message(req_method: ReqMethod) -> Message:
    return Message(
        id="req-1",
        type="req",
        channel_id="web",
        session_id="sess-1",
        params={},
        timestamp=0,
        ok=True,
        req_method=req_method,
        is_stream=True,
    )


def _control_message() -> Message:
    return Message(
        id="control-1",
        type="req",
        channel_id="feishu_enterprise",
        session_id="sess-1",
        params={"mode": "team"},
        timestamp=0,
        ok=True,
        req_method=ReqMethod.CHAT_SEND,
        is_stream=False,
    )


def test_processing_status_is_only_emitted_for_chat_streams() -> None:
    handler = _TestMessageHandler.create()

    assert handler.should_emit_processing_status_for_stream(
        _message(ReqMethod.CHAT_SEND)
    ) is True
    assert handler.should_emit_processing_status_for_stream(
        _message(ReqMethod.HISTORY_GET)
    ) is False


def test_queued_supplement_message_instructs_todo_continuation():
    handler = _TestMessageHandler.create()
    msg = _message(ReqMethod.CHAT_CANCEL)

    queued = handler.build_queued_chat_send_message(
        msg,
        "删除 todo 列表里的提出改善意见",
        original_request=r"Analyze C:\repo\src\ui\screen-layout.ts",
    )

    assert queued.params["supplement_input"] == "删除 todo 列表里的提出改善意见"
    assert queued.params["original_request"] == r"Analyze C:\repo\src\ui\screen-layout.ts"
    assert r"C:\repo\src\ui\screen-layout.ts" in queued.params["query"]
    assert "继续执行当前会话 todo 列表中仍未完成" in queued.params["query"]
    assert "不要因为补充请求本身处理完成就询问用户下一步" in queued.params["query"]
    assert "上一轮正在输出的任务结果可能只展示了一部分" in queued.params["query"]
    assert "不要仅因为 todo 状态已经变为 completed 就跳过" in queued.params["query"]


def test_chat_send_query_context_is_remembered_for_supplement():
    handler = _TestMessageHandler.create()
    msg = _message(ReqMethod.CHAT_SEND)
    msg.params = {
        "query": r"Read C:\repo\src\ui\screen-layout.ts and summarize it",
    }

    handler.remember_user_query_context(msg)

    assert (
        handler.get_session_last_user_query("sess-1")
        == r"Read C:\repo\src\ui\screen-layout.ts and summarize it"
    )


@pytest.mark.asyncio
async def test_handle_evolution_chunk_auto_accepts_previous_pending_approval() -> None:
    handler = _TestMessageHandler.create()
    handler.seed_pending_evolution_approval("sess-1", "team_skill_evolve_old")

    chunk = SimpleNamespace(
        channel_id="web",
        request_id="stream-1",
        payload={
            "event_type": "chat.ask_user_question",
            "request_id": "team_skill_evolve_new",
            "questions": [{"header": "x"}],
        },
    )

    await handler.handle_evolution_chunk(chunk, "sess-1", {"k": "v"})

    assert handler.pending_evolution_approval("sess-1") == "team_skill_evolve_new"
    auto_msg = handler.pop_user_message_nowait()
    assert auto_msg.session_id == "sess-1"
    assert auto_msg.channel_id == "web"
    assert auto_msg.params["request_id"] == "team_skill_evolve_old"
    assert auto_msg.params["answers"] == [{"selected_options": ["接收"]}]
    assert auto_msg.metadata == {"k": "v"}


def test_finish_evolution_approval_if_current_keeps_newer_pending_request() -> None:
    handler = _TestMessageHandler.create()
    handler.seed_pending_evolution_approval("sess-2", "team_skill_evolve_new")
    handler.seed_session_evolution_in_progress("sess-2")
    handler.seed_queued_supplement_input("sess-2", {"new_input": "follow up"})

    queued = handler.finish_evolution_approval_if_current(
        "sess-2",
        "team_skill_evolve_old",
    )

    assert queued is None
    assert handler.pending_evolution_approval("sess-2") == "team_skill_evolve_new"
    assert handler.has_session_evolution_in_progress("sess-2") is True
    assert handler.queued_supplement_input("sess-2") == {"new_input": "follow up"}


@pytest.mark.asyncio
async def test_control_command_cancel_suppresses_interrupt_result() -> None:
    handler = _TestMessageHandler.create()

    await handler.cancel_agent_work_for_session(
        _control_message(),
        "sess-1",
        publish_interrupt_result=False,
    )

    assert len(_FakeAgentClient.sent_requests) == 1
    assert await handler.consume_robot_messages(timeout=0) is None


@pytest.mark.asyncio
async def test_default_cancel_publishes_interrupt_result() -> None:
    handler = _TestMessageHandler.create()

    await handler.cancel_agent_work_for_session(_control_message(), "sess-1")

    out = await handler.consume_robot_messages(timeout=0)
    assert out is not None
    assert out.payload == _FakeAgentClient.response_payload
