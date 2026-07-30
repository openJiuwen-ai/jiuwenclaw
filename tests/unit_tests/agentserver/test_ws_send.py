import ast
import asyncio
import json
from pathlib import Path

import pytest

from jiuwenswarm.common.e2a.constants import E2A_WIRE_SERVER_PUSH_KEY
from jiuwenswarm.common.e2a.wire_codec import (
    encode_agent_chunk_for_wire,
    encode_agent_response_for_wire,
    parse_agent_server_wire_chunk,
)
from jiuwenswarm.common.schema.agent import (
    AgentRequest,
    AgentResponse,
    AgentResponseChunk,
)
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server import agent_ws_server
from jiuwenswarm.server import ws_send
from jiuwenswarm.server.gateway_push.wire import build_server_push_wire


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


@pytest.mark.asyncio
async def test_send_wire_payload_sends_small_wire_unchanged(monkeypatch):
    monkeypatch.setattr(ws_send, "AGENT_WS_SEND_BUDGET_BYTES", 1024)
    ws = FakeWebSocket()
    wire = {"request_id": "r1", "body": {"result": "ok"}}

    assert await ws_send.send_wire_payload(ws, wire) is True
    assert json.loads(ws.sent[0]) == wire


@pytest.mark.asyncio
async def test_send_wire_payload_counts_utf8_bytes(monkeypatch):
    """UTF-8 byte counting should work correctly with chunking."""
    wire = {"request_id": "r1", "body": {"result": "你" * 400}}
    character_size = len(json.dumps(wire, ensure_ascii=False))
    byte_size = len(json.dumps(wire, ensure_ascii=False).encode("utf-8"))
    monkeypatch.setattr(ws_send, "AGENT_WS_SEND_BUDGET_BYTES", 1200)
    ws = FakeWebSocket()

    assert character_size < 1200 < byte_size
    # With chunking, the payload should be split and sent successfully
    assert await ws_send.send_wire_payload(ws, wire) is True
    # Multiple chunks should be sent
    assert len(ws.sent) > 1
    # Each chunk should fit within the budget
    for sent in ws.sent:
        assert len(sent.encode("utf-8")) <= 1200


@pytest.mark.asyncio
async def test_oversized_unary_sends_chunked(monkeypatch):
    """Oversized unary messages should be chunked instead of sending error."""
    monkeypatch.setattr(ws_send, "AGENT_WS_SEND_BUDGET_BYTES", 2048)
    source = encode_agent_response_for_wire(
        AgentResponse(
            request_id="r-unary",
            channel_id="web",
            ok=True,
            payload={"content": "x" * 4096},
            agent_ref={"mode": "code", "id": "default"},
        ),
        response_id="r-unary",
    )
    source["session_id"] = "session-1"
    ws = FakeWebSocket()

    # Should succeed via chunking
    assert await ws_send.send_wire_payload(ws, source) is True
    # Multiple chunks should be sent
    assert len(ws.sent) > 1
    # Each chunk should fit within the budget
    for sent in ws.sent:
        assert len(sent.encode("utf-8")) <= 2048
    # First chunk should have routing keys preserved
    first_chunk = json.loads(ws.sent[0])
    assert first_chunk["request_id"] == "r-unary"
    assert first_chunk["session_id"] == "session-1"
    assert first_chunk["agent_ref"] == {"mode": "code", "id": "default"}
    # Should have chunking metadata
    assert "_chunking" in first_chunk.get("metadata", {})


@pytest.mark.asyncio
async def test_oversized_stream_sends_chunked(monkeypatch):
    """Oversized stream chunks should be chunked instead of sending error."""
    monkeypatch.setattr(ws_send, "AGENT_WS_SEND_BUDGET_BYTES", 2048)
    source = encode_agent_chunk_for_wire(
        AgentResponseChunk(
            request_id="r-stream",
            channel_id="web",
            payload={"event_type": "chat.tool_result", "result": "x" * 4096},
            is_complete=False,
            agent_ref={"mode": "team", "id": "team-1"},
        ),
        response_id="r-stream",
        sequence=7,
    )
    ws = FakeWebSocket()

    # Should succeed via chunking
    assert await ws_send.send_wire_payload(ws, source) is True
    # Multiple chunks should be sent
    assert len(ws.sent) > 1
    # Each chunk should fit within the budget
    for sent in ws.sent:
        assert len(sent.encode("utf-8")) <= 2048
    # First chunk should preserve routing keys
    first_chunk = json.loads(ws.sent[0])
    assert first_chunk["sequence"] == 7
    assert first_chunk["agent_ref"] == {"mode": "team", "id": "team-1"}
    # Should have chunking metadata
    assert "_chunking" in first_chunk.get("metadata", {})


@pytest.mark.asyncio
async def test_oversized_server_push_sends_chunked(monkeypatch):
    """Oversized server push messages should be chunked."""
    monkeypatch.setattr(ws_send, "AGENT_WS_SEND_BUDGET_BYTES", 2048)
    source = build_server_push_wire(
        {
            "request_id": "push-1",
            "channel_id": "web",
            "session_id": "session-push",
            "payload": {"result": "x" * 4096},
        }
    )
    ws = FakeWebSocket()

    # Should succeed via chunking
    assert await ws_send.send_wire_payload(ws, source) is True
    # Multiple chunks should be sent
    assert len(ws.sent) > 1
    # Each chunk should fit within the budget
    for sent in ws.sent:
        assert len(sent.encode("utf-8")) <= 2048
    # First chunk should preserve routing keys and push marker
    first_chunk = json.loads(ws.sent[0])
    assert first_chunk["session_id"] == "session-push"
    assert first_chunk["metadata"][E2A_WIRE_SERVER_PUSH_KEY] is True
    # Should have chunking metadata
    assert "_chunking" in first_chunk["metadata"]


@pytest.mark.asyncio
async def test_stream_stops_after_oversized_chunk_is_replaced(monkeypatch):
    class FakeAgent:
        async def process_message_stream(self, request):
            for index in range(2):
                yield AgentResponseChunk(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    payload={"content": str(index)},
                    is_complete=False,
                )

    server = agent_ws_server.AgentWebSocketServer.__new__(
        agent_ws_server.AgentWebSocketServer
    )
    server._session_stream_tasks = {}
    server._is_stateless_method_request = lambda request: True

    async def get_agent(channel_id):
        return FakeAgent()

    async def no_plan_exit_check(request, agent):
        return None

    send_count = 0

    async def replace_with_oversized_error(ws, wire):
        nonlocal send_count
        send_count += 1
        return False

    server._get_stateless_agent = get_agent
    server._check_post_process_plan_exit = no_plan_exit_check
    monkeypatch.setattr(
        agent_ws_server,
        "send_wire_payload",
        replace_with_oversized_error,
    )
    request = AgentRequest(
        request_id="stream-too-large",
        channel_id="web",
        session_id="session-1",
        req_method=ReqMethod.CHAT_SEND,
        params={},
        is_stream=True,
    )

    await server._handle_stream(FakeWebSocket(), request, asyncio.Lock())

    assert send_count == 1


def test_agent_ws_server_has_no_direct_websocket_send_calls():
    path = Path(agent_ws_server.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    direct_sends = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "send"
    ]

    assert direct_sends == []
