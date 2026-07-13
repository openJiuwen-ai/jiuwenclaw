import asyncio
import json
import logging

import pytest
from websockets.exceptions import ConnectionClosedError

from jiuwenswarm.common.e2a.gateway_normalize import (
    build_fallback_e2a,
    e2a_from_agent_fields,
)
from jiuwenswarm.common.schema.agent import AgentResponse
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


class ClosedFakeWebSocket:
    """模拟连接已断: 任何 send 都抛 ConnectionClosedError(no close frame received or sent)。"""

    remote_address = ("127.0.0.1", 1)

    async def send(self, payload):
        raise ConnectionClosedError(None, None)


class _AgentWsTestHarness(AgentWebSocketServer):
    """Test harness exposing protected _handle_message via a public wrapper."""

    async def handle_message_for_test(self, ws, raw, send_lock):
        await self._handle_message(ws, raw, send_lock)


class ClosedDuringUnaryServer(_AgentWsTestHarness):
    async def _handle_unary(self, ws, request, send_lock):
        raise ConnectionClosedError(None, None)


class _FakeInterruptAgent:
    async def process_message(self, request):
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload={"event_type": "chat.interrupt_result", "success": True},
        )


class _CleanupRecordingAgentManager:
    def __init__(self) -> None:
        self.cleaned: list[tuple[str, str]] = []
        self.agent = _FakeInterruptAgent()

    def get_agent_nowait(self, *_args, **_kwargs):
        return self.agent

    async def get_agent(self, **_kwargs):
        return self.agent

    async def cleanup_session_runtime(self, *, channel_id: str, session_id: str) -> bool:
        self.cleaned.append((channel_id, session_id))
        return True


class _NoCreateCleanupAgentManager:
    def __init__(self) -> None:
        self.cleaned: list[tuple[str, str]] = []

    def get_agent_nowait(self, *_args, **_kwargs):
        return None

    async def get_agent(self, **_kwargs):
        raise AssertionError("client disconnect cancel must not create an agent")

    async def cleanup_session_runtime(self, *, channel_id: str, session_id: str) -> bool:
        self.cleaned.append((channel_id, session_id))
        return False


async def _handle_cancel_cleanup_case(env) -> list[tuple[str, str]]:
    server = _AgentWsTestHarness.__new__(_AgentWsTestHarness)
    manager = _CleanupRecordingAgentManager()
    server._agent_manager = manager
    server._session_stream_tasks = {}

    await server.handle_message_for_test(
        FakeWebSocket(),
        json.dumps(env.to_dict(), ensure_ascii=False),
        asyncio.Lock(),
    )
    return manager.cleaned


@pytest.mark.asyncio
async def test_handle_message_treats_no_close_frame_as_disconnect(caplog):
    closed_exc = ConnectionClosedError(None, None)
    assert str(closed_exc) == "no close frame received or sent"

    target_logger = logging.getLogger("jiuwenswarm.server.agent_ws_server")
    target_logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger=target_logger.name)
    server = ClosedDuringUnaryServer()
    ws = FakeWebSocket()
    env = e2a_from_agent_fields(
        request_id="req-closed",
        channel_id="tui",
        session_id="sess-closed",
        req_method=ReqMethod.CONFIG_GET,
        params={},
        is_stream=False,
        timestamp=0.0,
    )

    try:
        await server.handle_message_for_test(
            ws,
            json.dumps(env.to_dict(), ensure_ascii=False),
            asyncio.Lock(),
        )
    finally:
        target_logger.removeHandler(caplog.handler)

    assert ws.sent == []
    assert "no close frame received or sent" in caplog.text
    assert "WebSocket 已关闭，放弃请求回包" in caplog.text
    assert "request_id=req-closed" in caplog.text
    assert "channel_id=tui" in caplog.text
    assert "exc_type='ConnectionClosedError'" in caplog.text
    assert "close_code=1006" in caplog.text
    assert "处理请求失败" not in caplog.text


@pytest.mark.asyncio
async def test_handle_message_does_not_raise_on_closed_ws_during_json_parse_error(caplog):
    """连接已断时, 收到非法 JSON 的回包 send 抛 ConnectionClosedError 不应逃逸出 _handle_message.

    也不应记 ERROR traceback; 应记 INFO 并静默放弃回包.
    """
    target_logger = logging.getLogger("jiuwenswarm.server.agent_ws_server")
    target_logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger=target_logger.name)

    server = _AgentWsTestHarness.__new__(_AgentWsTestHarness)
    ws = ClosedFakeWebSocket()

    try:
        await server.handle_message_for_test(
            ws,
            "not-a-json-payload{",
            asyncio.Lock(),
        )
    finally:
        target_logger.removeHandler(caplog.handler)

    assert "JSON 解析错误未发送" in caplog.text
    # 不应走通用 ERROR 路径
    assert "处理请求失败" not in caplog.text
    assert "连接处理异常" not in caplog.text


@pytest.mark.asyncio
async def test_handle_message_sends_json_parse_error_when_ws_open(caplog):
    """连接正常时, 非法 JSON 仍应正常回包 parse-error, 修复不应破坏该行为。"""
    target_logger = logging.getLogger("jiuwenswarm.server.agent_ws_server")
    target_logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger=target_logger.name)

    server = _AgentWsTestHarness.__new__(_AgentWsTestHarness)
    ws = FakeWebSocket()

    try:
        await server.handle_message_for_test(
            ws,
            "not-a-json-payload{",
            asyncio.Lock(),
        )
    finally:
        target_logger.removeHandler(caplog.handler)

    assert len(ws.sent) == 1
    frame = ws.sent[0]
    assert frame.get("status") == "failed"
    assert "JSON 解析失败" in frame.get("body", {}).get("message", "")
    assert "JSON 解析错误未发送" not in caplog.text


@pytest.mark.asyncio
async def test_disconnect_cancel_cleans_session_runtime_after_cancel() -> None:
    env = e2a_from_agent_fields(
        request_id="req-disconnect-cancel",
        channel_id="tui",
        session_id="sess-exit",
        req_method=ReqMethod.CHAT_CANCEL,
        params={
            "intent": "cancel",
            "session_id": "sess-exit",
        },
        is_stream=False,
        timestamp=0.0,
    )
    env.channel_context["_jiuwenswarm_cancel_source"] = "client_disconnect"

    assert await _handle_cancel_cleanup_case(env) == [("tui", "sess-exit")]


@pytest.mark.asyncio
async def test_disconnect_cancel_does_not_create_agent_when_runtime_missing() -> None:
    server = _AgentWsTestHarness.__new__(_AgentWsTestHarness)
    manager = _NoCreateCleanupAgentManager()
    server._agent_manager = manager
    server._session_stream_tasks = {}
    ws = FakeWebSocket()
    env = e2a_from_agent_fields(
        request_id="req-disconnect-no-agent",
        channel_id="tui",
        session_id="sess-no-agent",
        req_method=ReqMethod.CHAT_CANCEL,
        params={"intent": "cancel", "session_id": "sess-no-agent"},
        is_stream=False,
        timestamp=0.0,
    )
    env.channel_context["_jiuwenswarm_cancel_source"] = "client_disconnect"

    await server.handle_message_for_test(
        ws,
        json.dumps(env.to_dict(), ensure_ascii=False),
        asyncio.Lock(),
    )

    assert manager.cleaned == [("tui", "sess-no-agent")]
    assert len(ws.sent) == 1


@pytest.mark.asyncio
async def test_disconnect_cancel_cleans_session_runtime_when_cancel_reply_send_fails() -> None:
    server = _AgentWsTestHarness.__new__(_AgentWsTestHarness)
    manager = _CleanupRecordingAgentManager()
    server._agent_manager = manager
    server._session_stream_tasks = {}
    env = e2a_from_agent_fields(
        request_id="req-disconnect-cancel-send-fails",
        channel_id="tui",
        session_id="sess-send-fails",
        req_method=ReqMethod.CHAT_CANCEL,
        params={"intent": "cancel", "session_id": "sess-send-fails"},
        is_stream=False,
        timestamp=0.0,
    )
    env.channel_context["_jiuwenswarm_cancel_source"] = "client_disconnect"

    await server.handle_message_for_test(
        ClosedFakeWebSocket(),
        json.dumps(env.to_dict(), ensure_ascii=False),
        asyncio.Lock(),
    )

    assert manager.cleaned == [("tui", "sess-send-fails")]


@pytest.mark.asyncio
async def test_disconnect_cancel_cleans_session_runtime_when_stream_task_cleanup_fails() -> None:
    async def failing_stream_task() -> None:
        try:
            await asyncio.sleep(60)
        finally:
            raise RuntimeError("stream cleanup failed")

    server = _AgentWsTestHarness.__new__(_AgentWsTestHarness)
    manager = _CleanupRecordingAgentManager()
    server._agent_manager = manager
    stream_task = asyncio.create_task(failing_stream_task())
    server._session_stream_tasks = {"sess-stream-cleanup-fails": stream_task}
    env = e2a_from_agent_fields(
        request_id="req-disconnect-stream-cleanup-fails",
        channel_id="tui",
        session_id="sess-stream-cleanup-fails",
        req_method=ReqMethod.CHAT_CANCEL,
        params={
            "intent": "cancel",
            "session_id": "sess-stream-cleanup-fails",
        },
        is_stream=False,
        timestamp=0.0,
    )
    env.channel_context["_jiuwenswarm_cancel_source"] = "client_disconnect"

    await server.handle_message_for_test(
        FakeWebSocket(),
        json.dumps(env.to_dict(), ensure_ascii=False),
        asyncio.Lock(),
    )

    assert manager.cleaned == [("tui", "sess-stream-cleanup-fails")]
    assert stream_task.done() is True


@pytest.mark.asyncio
async def test_cancel_source_param_does_not_trigger_session_runtime_cleanup() -> None:
    env = e2a_from_agent_fields(
        request_id="req-param-source",
        channel_id="tui",
        session_id="sess-param",
        req_method=ReqMethod.CHAT_CANCEL,
        params={
            "intent": "cancel",
            "session_id": "sess-param",
            "cancel_source": "client_disconnect",
        },
        is_stream=False,
        timestamp=0.0,
    )

    assert await _handle_cancel_cleanup_case(env) == []


@pytest.mark.asyncio
async def test_cancel_source_metadata_does_not_trigger_supplement_runtime_cleanup() -> None:
    env = e2a_from_agent_fields(
        request_id="req-metadata-source",
        channel_id="tui",
        session_id="sess-metadata",
        req_method=ReqMethod.CHAT_CANCEL,
        params={
            "intent": "supplement",
            "session_id": "sess-metadata",
        },
        is_stream=False,
        timestamp=0.0,
        metadata={"_jiuwenswarm_cancel_source": "client_disconnect"},
    )

    assert await _handle_cancel_cleanup_case(env) == []


@pytest.mark.asyncio
async def test_legacy_metadata_cancel_source_does_not_trigger_runtime_cleanup() -> None:
    env = build_fallback_e2a(
        {
            "request_id": "req-legacy-metadata-source",
            "channel_id": "tui",
            "session_id": "sess-legacy-metadata",
            "req_method": ReqMethod.CHAT_CANCEL.value,
            "params": {
                "intent": "cancel",
                "session_id": "sess-legacy-metadata",
            },
            "is_stream": False,
            "timestamp": 0.0,
            "metadata": {"_jiuwenswarm_cancel_source": "client_disconnect"},
        }
    )

    assert await _handle_cancel_cleanup_case(env) == []


@pytest.mark.asyncio
async def test_manual_cancel_keeps_session_runtime() -> None:
    env = e2a_from_agent_fields(
        request_id="req-manual-cancel",
        channel_id="tui",
        session_id="sess-keep",
        req_method=ReqMethod.CHAT_CANCEL,
        params={"intent": "cancel", "session_id": "sess-keep"},
        is_stream=False,
        timestamp=0.0,
    )

    assert await _handle_cancel_cleanup_case(env) == []
