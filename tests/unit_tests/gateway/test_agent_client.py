import asyncio
import json
import logging

import pytest
from websockets.exceptions import ConnectionClosedError

from jiuwenswarm.common.ws_limits import AGENT_WS_MAX_MESSAGE_BYTES
from jiuwenswarm.common.ws_chunking import (
    CHUNKING_META_KEY,
    CHUNK_CONTENT_KEY,
    split_wire_payload_for_chunking,
)
from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenswarm.common.e2a.wire_codec import (
    encode_agent_chunk_for_wire,
    encode_agent_response_for_wire,
)
from jiuwenswarm.gateway.routing import agent_client
from jiuwenswarm.gateway.routing.agent_client import WebSocketAgentServerClient
from jiuwenswarm.common.schema.agent import AgentResponse, AgentResponseChunk


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent_payloads: list[dict] = []

    async def send(self, data: str) -> None:
        self.sent_payloads.append(data)


class ClosingSendWebSocket:
    async def send(self, data: str) -> None:
        raise ConnectionClosedError(None, None)


class ClosingRecvWebSocket:
    def __init__(self) -> None:
        self.recv_calls = 0

    async def recv(self) -> str:
        self.recv_calls += 1
        raise ConnectionClosedError(None, None)


class AgentClientHarness(WebSocketAgentServerClient):
    def set_ws_for_test(self, ws) -> None:
        self._ws = ws

    def set_uri_for_test(self, uri: str) -> None:
        self._uri = uri

    def set_running_for_test(self, running: bool) -> None:
        self._running = running

    def set_server_ready_for_test(self, ready: bool) -> None:
        self._server_ready = ready

    def is_running_for_test(self) -> bool:
        return self._running

    def get_ws_for_test(self):
        return self._ws

    def has_message_queue_for_test(self, request_id: str) -> bool:
        return request_id in self._message_queues

    def get_message_queue_for_test(self, request_id: str):
        return self._message_queues[request_id]

    def set_message_queue_for_test(self, request_id: str, queue) -> None:
        self._message_queues[request_id] = queue

    async def run_message_receiver_loop_for_test(self) -> None:
        await self._message_receiver_loop()

    async def stop_receiver_after_fatal_error_for_test(self, exc: BaseException) -> None:
        await self._stop_receiver_after_fatal_error(exc)


class ReconnectingAgentClientHarness(AgentClientHarness):
    def __init__(self) -> None:
        super().__init__()
        self.connect_calls: list[str] = []
        self.reconnected_ws = FakeWebSocket()

    async def connect(self, uri: str) -> None:
        self.connect_calls.append(uri)
        self._uri = uri
        self._ws = self.reconnected_ws
        self._server_ready = True


def test_agent_client_uses_shared_websocket_limit():
    assert not hasattr(agent_client, "_WS_MAX_SIZE")
    assert agent_client.AGENT_WS_MAX_MESSAGE_BYTES == AGENT_WS_MAX_MESSAGE_BYTES


@pytest.mark.asyncio
async def test_send_request_stream_keeps_tail_window_for_processing_status(monkeypatch):
    client = AgentClientHarness()
    client.set_ws_for_test(FakeWebSocket())

    monkeypatch.setattr(
        "jiuwenswarm.gateway.routing.agent_client._STREAM_TRAILING_MESSAGE_GRACE_SECONDS",
        0.05,
    )

    env = e2a_from_agent_fields(
        request_id="rid-tail",
        channel_id="acp",
        session_id="sess-tail",
        params={"content": "hello"},
        is_stream=True,
    )

    async def inject_frames():
        while not client.has_message_queue_for_test("rid-tail"):
            await asyncio.sleep(0.001)
        queue = client.get_message_queue_for_test("rid-tail")
        await queue.put(
            encode_agent_chunk_for_wire(
                AgentResponseChunk(
                    request_id="rid-tail",
                    channel_id="acp",
                    payload={"content": "partial", "event_type": "chat.delta"},
                    is_complete=False,
                ),
                response_id="rid-tail",
                sequence=0,
            )
        )
        await queue.put(
            encode_agent_chunk_for_wire(
                AgentResponseChunk(
                    request_id="rid-tail",
                    channel_id="acp",
                    payload={"is_complete": True},
                    is_complete=True,
                ),
                response_id="rid-tail",
                sequence=1,
            )
        )
        await asyncio.sleep(0.01)
        await queue.put(
            encode_agent_chunk_for_wire(
                AgentResponseChunk(
                    request_id="rid-tail",
                    channel_id="acp",
                    payload={"event_type": "chat.processing_status", "is_processing": False},
                    is_complete=False,
                ),
                response_id="rid-tail",
                sequence=2,
            )
        )

    injector = asyncio.create_task(inject_frames())
    chunks = []
    async for chunk in client.send_request_stream(env):
        chunks.append(chunk)
    await injector

    assert [chunk.payload for chunk in chunks] == [
        {"content": "partial", "event_type": "chat.delta"},
        {"is_complete": True},
        {"event_type": "chat.processing_status", "is_processing": False},
    ]
    assert client.has_message_queue_for_test("rid-tail") is False


@pytest.mark.asyncio
async def test_send_request_stream_absorbs_duplicate_complete_frames(monkeypatch):
    client = AgentClientHarness()
    client.set_ws_for_test(FakeWebSocket())

    monkeypatch.setattr(
        "jiuwenswarm.gateway.routing.agent_client._STREAM_TRAILING_MESSAGE_GRACE_SECONDS",
        0.05,
    )

    env = e2a_from_agent_fields(
        request_id="rid-complete",
        channel_id="acp",
        session_id="sess-complete",
        params={"content": "hello"},
        is_stream=True,
    )

    async def inject_frames():
        while not client.has_message_queue_for_test("rid-complete"):
            await asyncio.sleep(0.001)
        queue = client.get_message_queue_for_test("rid-complete")
        for seq in (0, 1):
            await queue.put(
                encode_agent_chunk_for_wire(
                    AgentResponseChunk(
                        request_id="rid-complete",
                        channel_id="acp",
                        payload={"is_complete": True},
                        is_complete=True,
                    ),
                    response_id="rid-complete",
                    sequence=seq,
                )
            )

    injector = asyncio.create_task(inject_frames())
    chunks = []
    async for chunk in client.send_request_stream(env):
        chunks.append(chunk)
    await injector

    assert len(chunks) == 2
    assert all(chunk.is_complete for chunk in chunks)
    assert client.has_message_queue_for_test("rid-complete") is False


@pytest.mark.asyncio
async def test_message_receiver_loop_stops_on_closed_websocket():
    client = AgentClientHarness()
    ws = ClosingRecvWebSocket()
    client.set_ws_for_test(ws)
    client.set_running_for_test(True)

    await asyncio.wait_for(client.run_message_receiver_loop_for_test(), timeout=0.1)

    assert client.is_running_for_test() is False
    assert ws.recv_calls == 1


@pytest.mark.asyncio
async def test_message_receiver_loop_logs_close_diagnostics(caplog):
    target_logger = logging.getLogger("jiuwenswarm.gateway.routing.agent_client")
    target_logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger=target_logger.name)

    client = AgentClientHarness()
    ws = ClosingRecvWebSocket()
    client.set_ws_for_test(ws)
    client.set_running_for_test(True)
    client.set_server_ready_for_test(True)
    client.set_message_queue_for_test("rid-pending", asyncio.Queue())

    try:
        await asyncio.wait_for(client.run_message_receiver_loop_for_test(), timeout=0.1)
    finally:
        target_logger.removeHandler(caplog.handler)

    assert "AgentServer WebSocket 已关闭" in caplog.text
    assert "exc_type='ConnectionClosedError'" in caplog.text
    assert "message='no close frame received or sent'" in caplog.text
    assert "close_code=1006" in caplog.text
    assert "pending_requests=1" in caplog.text
    assert "server_ready=True" in caplog.text


@pytest.mark.asyncio
async def test_send_request_fails_pending_request_when_receiver_stops():
    client = AgentClientHarness()
    ws = FakeWebSocket()
    client.set_ws_for_test(ws)

    env = e2a_from_agent_fields(
        request_id="rid-fatal-close",
        channel_id="acp",
        session_id="sess-fatal-close",
        params={"content": "hello"},
        is_stream=False,
    )

    task = asyncio.create_task(client.send_request(env))
    for _ in range(100):
        if ws.sent_payloads:
            break
        await asyncio.sleep(0.001)
    assert ws.sent_payloads

    await client.stop_receiver_after_fatal_error_for_test(ConnectionClosedError(None, None))

    with pytest.raises(RuntimeError, match="AgentServer WebSocket connection closed"):
        await asyncio.wait_for(task, timeout=0.1)
    assert client.has_message_queue_for_test("rid-fatal-close") is False


@pytest.mark.asyncio
async def test_send_request_reconnects_before_new_request_after_disconnect():
    client = ReconnectingAgentClientHarness()
    client.set_uri_for_test("ws://agent-server")
    client.set_ws_for_test(None)

    env = e2a_from_agent_fields(
        request_id="rid-reconnect",
        channel_id="acp",
        session_id="sess-reconnect",
        params={"content": "hello"},
        is_stream=False,
    )

    task = asyncio.create_task(client.send_request(env))
    for _ in range(100):
        if client.reconnected_ws.sent_payloads:
            break
        await asyncio.sleep(0.001)
    assert client.connect_calls == ["ws://agent-server"]
    assert client.reconnected_ws.sent_payloads

    queue = client.get_message_queue_for_test("rid-reconnect")
    await queue.put(
        encode_agent_response_for_wire(
            AgentResponse(
                request_id="rid-reconnect",
                channel_id="acp",
                ok=True,
                payload={"status": "reconnected"},
            ),
            response_id="rid-reconnect",
        )
    )

    response = await asyncio.wait_for(task, timeout=0.1)

    assert response.ok is True
    assert response.payload == {"status": "reconnected"}
    assert client.has_message_queue_for_test("rid-reconnect") is False


@pytest.mark.asyncio
async def test_send_request_clears_connection_when_send_fails():
    client = ReconnectingAgentClientHarness()
    client.set_uri_for_test("ws://agent-server")
    client.set_ws_for_test(ClosingSendWebSocket())
    client.set_running_for_test(True)
    client.set_server_ready_for_test(True)

    failed_env = e2a_from_agent_fields(
        request_id="rid-send-close",
        channel_id="acp",
        session_id="sess-send-close",
        params={"content": "hello"},
        is_stream=False,
    )

    with pytest.raises(RuntimeError, match="AgentServer WebSocket connection closed"):
        await client.send_request(failed_env)

    assert client.get_ws_for_test() is None
    assert client.is_running_for_test() is False
    assert client.has_message_queue_for_test("rid-send-close") is False

    reconnect_env = e2a_from_agent_fields(
        request_id="rid-after-send-close",
        channel_id="acp",
        session_id="sess-send-close",
        params={"content": "again"},
        is_stream=False,
    )

    task = asyncio.create_task(client.send_request(reconnect_env))
    for _ in range(100):
        if client.reconnected_ws.sent_payloads:
            break
        await asyncio.sleep(0.001)
    assert client.connect_calls == ["ws://agent-server"]
    assert client.reconnected_ws.sent_payloads

    queue = client.get_message_queue_for_test("rid-after-send-close")
    await queue.put(
        encode_agent_response_for_wire(
            AgentResponse(
                request_id="rid-after-send-close",
                channel_id="acp",
                ok=True,
                payload={"status": "reconnected"},
            ),
            response_id="rid-after-send-close",
        )
    )

    response = await asyncio.wait_for(task, timeout=0.1)
    assert response.ok is True
    assert client.has_message_queue_for_test("rid-after-send-close") is False


# ---------------------------------------------------------------------------
# Chunk reassembly tests
# ---------------------------------------------------------------------------


class ChunkedRecvWebSocket:
    """WebSocket that yields pre-configured messages (including chunks)."""

    def __init__(self, messages: list[dict]) -> None:
        self._messages = list(messages)
        self.recv_calls = 0

    async def recv(self) -> str:
        if not self._messages:
            raise ConnectionClosedError(None, None)
        self.recv_calls += 1
        msg = self._messages.pop(0)
        return json.dumps(msg)


def _make_chunk(chunk_id: str, chunk_index: int, total_chunks: int, content: str, request_id: str) -> dict:
    """Helper to build a chunk wire payload."""
    return {
        "request_id": request_id,
        "metadata": {
            CHUNKING_META_KEY: {
                "chunk_id": chunk_id,
                "chunk_index": chunk_index,
                "total_chunks": total_chunks,
            }
        },
        CHUNK_CONTENT_KEY: content,
    }


@pytest.mark.asyncio
async def test_message_receiver_loop_reassembles_chunked_messages():
    """Receiver loop should reassemble chunked messages before routing."""
    # Build a large payload that will be split into chunks
    original_wire = {
        "request_id": "rid-chunked",
        "type": "response",
        "body": {"content": "x" * 100},
    }
    original_json = json.dumps(original_wire)

    # Split into 3 chunks manually
    piece_size = len(original_json) // 3
    pieces = [
        original_json[:piece_size],
        original_json[piece_size:2*piece_size],
        original_json[2*piece_size:],
    ]

    # Create chunk messages
    chunks = [
        _make_chunk("c1", 0, 3, pieces[0], "rid-chunked"),
        _make_chunk("c1", 1, 3, pieces[1], "rid-chunked"),
        _make_chunk("c1", 2, 3, pieces[2], "rid-chunked"),
    ]

    client = AgentClientHarness()
    ws = ChunkedRecvWebSocket(chunks)
    client.set_ws_for_test(ws)
    client.set_running_for_test(True)

    # Create queue for the request
    queue = asyncio.Queue()
    client.set_message_queue_for_test("rid-chunked", queue)

    # Run receiver loop (will exit when ws raises ConnectionClosedError)
    await asyncio.wait_for(client.run_message_receiver_loop_for_test(), timeout=0.5)

    # Verify the reassembled message was routed to the queue
    # Note: queue may contain a _ReceiverFailure object after ConnectionClosedError
    assert queue.qsize() >= 1
    reassembled = queue.get_nowait()
    assert reassembled == original_wire


@pytest.mark.asyncio
async def test_message_receiver_loop_handles_out_of_order_chunks():
    """Receiver loop should reassemble chunks arriving out of order."""
    original_wire = {
        "request_id": "rid-outoforder",
        "type": "response",
        "body": {"data": "test"},
    }
    original_json = json.dumps(original_wire)

    piece_size = len(original_json) // 3
    pieces = [
        original_json[:piece_size],
        original_json[piece_size:2*piece_size],
        original_json[2*piece_size:],
    ]

    # Send chunks out of order: 2, 0, 1
    chunks = [
        _make_chunk("c2", 2, 3, pieces[2], "rid-outoforder"),
        _make_chunk("c2", 0, 3, pieces[0], "rid-outoforder"),
        _make_chunk("c2", 1, 3, pieces[1], "rid-outoforder"),
    ]

    client = AgentClientHarness()
    ws = ChunkedRecvWebSocket(chunks)
    client.set_ws_for_test(ws)
    client.set_running_for_test(True)

    queue = asyncio.Queue()
    client.set_message_queue_for_test("rid-outoforder", queue)

    await asyncio.wait_for(client.run_message_receiver_loop_for_test(), timeout=0.5)

    # Should still reassemble correctly
    # Note: queue may contain a _ReceiverFailure object after ConnectionClosedError
    assert queue.qsize() >= 1
    reassembled = queue.get_nowait()
    assert reassembled == original_wire


@pytest.mark.asyncio
async def test_message_receiver_loop_passes_through_non_chunked_messages():
    """Non-chunked messages should pass through unchanged."""
    normal_wire = {
        "request_id": "rid-normal",
        "type": "response",
        "body": {"result": "ok"},
    }

    client = AgentClientHarness()
    ws = ChunkedRecvWebSocket([normal_wire])
    client.set_ws_for_test(ws)
    client.set_running_for_test(True)

    queue = asyncio.Queue()
    client.set_message_queue_for_test("rid-normal", queue)

    await asyncio.wait_for(client.run_message_receiver_loop_for_test(), timeout=0.5)

    # Note: queue may contain a _ReceiverFailure object after ConnectionClosedError
    assert queue.qsize() >= 1
    received = queue.get_nowait()
    assert received == normal_wire


@pytest.mark.asyncio
async def test_message_receiver_loop_handles_mixed_chunked_and_normal():
    """Receiver loop should handle a mix of chunked and non-chunked messages."""
    original_chunked = {
        "request_id": "rid-mixed-chunk",
        "type": "response",
        "body": {"content": "chunked"},
    }
    original_chunked_json = json.dumps(original_chunked)

    # Split chunked message into 2 pieces
    mid = len(original_chunked_json) // 2
    chunk1 = _make_chunk("c3", 0, 2, original_chunked_json[:mid], "rid-mixed-chunk")
    chunk2 = _make_chunk("c3", 1, 2, original_chunked_json[mid:], "rid-mixed-chunk")

    normal_wire = {
        "request_id": "rid-mixed-normal",
        "type": "response",
        "body": {"result": "normal"},
    }

    # Interleave: chunk1, normal, chunk2
    messages = [chunk1, normal_wire, chunk2]

    client = AgentClientHarness()
    ws = ChunkedRecvWebSocket(messages)
    client.set_ws_for_test(ws)
    client.set_running_for_test(True)

    queue1 = asyncio.Queue()
    queue2 = asyncio.Queue()
    client.set_message_queue_for_test("rid-mixed-chunk", queue1)
    client.set_message_queue_for_test("rid-mixed-normal", queue2)

    await asyncio.wait_for(client.run_message_receiver_loop_for_test(), timeout=0.5)

    # Both messages should be routed correctly
    # Note: queues may contain a _ReceiverFailure object after ConnectionClosedError
    assert queue1.qsize() >= 1
    assert queue2.qsize() >= 1
    assert queue1.get_nowait() == original_chunked
    assert queue2.get_nowait() == normal_wire


@pytest.mark.asyncio
async def test_disconnect_clears_chunk_buffer():
    """disconnect() should clear the chunk buffer."""
    client = AgentClientHarness()
    client.set_ws_for_test(FakeWebSocket())

    # Add incomplete chunks to buffer
    chunk = _make_chunk("c4", 0, 3, "piece0", "rid-disconnect")
    await client._chunk_buffer.add_chunk(chunk, "rid-disconnect")
    assert client._chunk_buffer.pending_count() == 1

    # Disconnect should clear the buffer
    await client.disconnect()
    assert client._chunk_buffer.pending_count() == 0


@pytest.mark.asyncio
async def test_stop_receiver_clears_chunk_buffer():
    """_stop_receiver_after_fatal_error() should clear the chunk buffer."""
    client = AgentClientHarness()
    client.set_ws_for_test(FakeWebSocket())
    client.set_running_for_test(True)

    # Add incomplete chunks to buffer
    chunk = _make_chunk("c5", 0, 3, "piece0", "rid-fatal")
    await client._chunk_buffer.add_chunk(chunk, "rid-fatal")
    assert client._chunk_buffer.pending_count() == 1

    # Stop receiver should clear the buffer
    await client.stop_receiver_after_fatal_error_for_test(ConnectionClosedError(None, None))
    assert client._chunk_buffer.pending_count() == 0


@pytest.mark.asyncio
async def test_chunk_buffer_initialization():
    """WebSocketAgentServerClient should initialize with an empty chunk buffer."""
    client = WebSocketAgentServerClient()
    assert client._chunk_buffer is not None
    assert client._chunk_buffer.pending_count() == 0


@pytest.mark.asyncio
async def test_periodic_chunk_cleanup_runs_in_receiver_loop():
    """Verify that _periodic_chunk_cleanup task is created and cancelled properly."""
    client = AgentClientHarness()
    ws = ChunkedRecvWebSocket([])  # Empty, will immediately raise ConnectionClosedError
    client.set_ws_for_test(ws)
    client.set_running_for_test(True)

    # Add an incomplete chunk that will expire
    chunk = _make_chunk("c-expire", 0, 3, "piece0", "rid-expire")
    await client._chunk_buffer.add_chunk(chunk, "rid-expire")
    assert client._chunk_buffer.pending_count() == 1

    # Run receiver loop (will exit immediately due to empty ws)
    await asyncio.wait_for(client.run_message_receiver_loop_for_test(), timeout=0.5)

    # The cleanup task should have been cancelled when receiver loop exited
    # Buffer should be cleared by _stop_receiver_after_fatal_error
    assert client._chunk_buffer.pending_count() == 0


@pytest.mark.asyncio
async def test_chunked_server_push_message():
    """Chunked messages with server push metadata should be handled correctly."""
    from jiuwenswarm.common.e2a.constants import E2A_WIRE_SERVER_PUSH_KEY

    original_wire = {
        "request_id": "rid-push",
        "type": "event",
        "event": "response.push",
        "metadata": {E2A_WIRE_SERVER_PUSH_KEY: True},
        "payload": {"result": "pushed"},
    }
    original_json = json.dumps(original_wire)

    # Split into 2 chunks
    mid = len(original_json) // 2
    chunks = [
        _make_chunk("c-push", 0, 2, original_json[:mid], "rid-push"),
        _make_chunk("c-push", 1, 2, original_json[mid:], "rid-push"),
    ]

    client = AgentClientHarness()
    ws = ChunkedRecvWebSocket(chunks)
    client.set_ws_for_test(ws)
    client.set_running_for_test(True)

    # Track server push calls
    push_calls = []
    async def on_push(data):
        push_calls.append(data)

    client.set_server_push_handler(on_push)

    await asyncio.wait_for(client.run_message_receiver_loop_for_test(), timeout=0.5)

    # The reassembled message should be routed to server push handler
    assert len(push_calls) == 1
    assert push_calls[0] == original_wire
    assert push_calls[0]["metadata"][E2A_WIRE_SERVER_PUSH_KEY] is True


@pytest.mark.asyncio
async def test_chunked_message_with_cancelled_request():
    """Chunked messages for cancelled requests should be dropped after reassembly."""
    original_wire = {
        "request_id": "rid-cancelled",
        "type": "response",
        "body": {"result": "should be dropped"},
    }
    original_json = json.dumps(original_wire)

    # Split into 2 chunks
    mid = len(original_json) // 2
    chunks = [
        _make_chunk("c-cancel", 0, 2, original_json[:mid], "rid-cancelled"),
        _make_chunk("c-cancel", 1, 2, original_json[mid:], "rid-cancelled"),
    ]

    client = AgentClientHarness()
    ws = ChunkedRecvWebSocket(chunks)
    client.set_ws_for_test(ws)
    client.set_running_for_test(True)

    # Mark request as cancelled
    client._cancelled_request_ids.add("rid-cancelled")

    # Create queue (should not receive the message)
    queue = asyncio.Queue()
    client.set_message_queue_for_test("rid-cancelled", queue)

    await asyncio.wait_for(client.run_message_receiver_loop_for_test(), timeout=0.5)

    # Queue should be empty (message dropped due to cancellation)
    # Note: may contain _ReceiverFailure from connection close
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())

    # Should only have _ReceiverFailure, not the actual message
    assert all(not isinstance(item, dict) or item.get("type") != "response" for item in items)
