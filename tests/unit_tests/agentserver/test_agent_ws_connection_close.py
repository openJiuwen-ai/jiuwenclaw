import asyncio
import json
import logging

import pytest
from websockets.exceptions import ConnectionClosedError

from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
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
