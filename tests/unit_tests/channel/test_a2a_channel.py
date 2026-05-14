import asyncio
import time

import pytest

from jiuwenclaw.gateway.channel_manager.protocol.a2a.a2a_connect import A2AChannel, A2AChannelConfig
from jiuwenclaw.common.schema.message import EventType, Message


class DummyBus:
    @staticmethod
    async def publish_user_messages(msg):
        return None


class DummyPart:
    def __init__(
        self,
        *,
        text: str = "",
        filename: str = "",
        media_type: str = "",
        url: str = "",
        data: str = "",
        raw: str = "",
    ):
        self.text = text
        self.filename = filename
        self.media_type = media_type
        self.url = url
        self.data = data
        self.raw = raw


class DummyA2AMessage:
    def __init__(self, parts):
        self.parts = parts


def build_channel() -> A2AChannel:
    return A2AChannel(A2AChannelConfig(enabled=False), DummyBus())


def test_map_a2a_parts_to_params_text_and_files():
    msg = DummyA2AMessage(
        [
            DummyPart(text="hello"),
            DummyPart(
                filename="sample-url.txt",
                media_type="text/plain",
                url="https://example.com/test.txt",
            ),
            DummyPart(
                filename="inline.txt",
                media_type="text/plain",
                data="aGVsbG8gd29ybGQ=",
            ),
            DummyPart(raw="opaque-bytes"),
        ]
    )

    query, files = A2AChannel.map_a2a_parts_to_params(msg)

    assert query == "hello"
    assert len(files) == 3
    assert files[0]["filename"] == "sample-url.txt"
    assert files[0]["url"] == "https://example.com/test.txt"
    assert files[0]["uri"] == "https://example.com/test.txt"
    assert files[1]["filename"] == "inline.txt"
    assert files[1]["data"] == "aGVsbG8gd29ybGQ="
    assert files[1]["encoding"] == "base64"
    # raw-only part should still produce a normalized synthetic filename.
    assert files[2]["filename"] == "a2a_part_3"
    assert files[2]["raw"] == "opaque-bytes"


def test_message_to_a2a_parts_filters_completion_sentinel_text():
    msg = Message(
        id="r1",
        type="event",
        channel_id="a2a",
        session_id="s1",
        params={},
        timestamp=time.time(),
        ok=True,
        payload={"content": "{'is_complete': True}"},
        event_type=EventType.CHAT_FINAL,
    )

    parts = A2AChannel.message_to_a2a_parts(msg, fallback_to_text=False)
    assert parts == []


def test_message_to_a2a_parts_maps_tool_events():
    tool_call_msg = Message(
        id="r2",
        type="event",
        channel_id="a2a",
        session_id="s1",
        params={},
        timestamp=time.time(),
        ok=True,
        payload={"tool_call": {"name": "view_file"}},
        event_type=EventType.CHAT_TOOL_CALL,
    )
    tool_result_msg = Message(
        id="r3",
        type="event",
        channel_id="a2a",
        session_id="s1",
        params={},
        timestamp=time.time(),
        ok=True,
        payload={"tool_name": "view_file", "result": "ok"},
        event_type=EventType.CHAT_TOOL_RESULT,
    )

    call_parts = A2AChannel.message_to_a2a_parts(tool_call_msg, fallback_to_text=False)
    result_parts = A2AChannel.message_to_a2a_parts(tool_result_msg, fallback_to_text=False)

    assert len(call_parts) == 1
    assert getattr(call_parts[0], "text", "") == "[tool_call] view_file"
    assert len(result_parts) == 2
    assert getattr(result_parts[0], "text", "") == "ok"
    assert getattr(result_parts[1], "text", "") == "[tool_result:view_file] ok"


def test_is_terminal_message_rules():
    terminal_error = Message(
        id="e1",
        type="event",
        channel_id="a2a",
        session_id="s1",
        params={},
        timestamp=time.time(),
        ok=False,
        payload={"error": "boom"},
        event_type=EventType.CHAT_ERROR,
    )
    terminal_complete = Message(
        id="e2",
        type="event",
        channel_id="a2a",
        session_id="s1",
        params={},
        timestamp=time.time(),
        ok=True,
        payload={"is_complete": True},
        event_type=EventType.CHAT_FINAL,
    )
    non_terminal = Message(
        id="e3",
        type="event",
        channel_id="a2a",
        session_id="s1",
        params={},
        timestamp=time.time(),
        ok=True,
        payload={"content": "delta"},
        event_type=EventType.CHAT_DELTA,
    )

    assert A2AChannel.is_terminal_message(terminal_error) is True
    assert A2AChannel.is_terminal_message(terminal_complete) is True
    assert A2AChannel.is_terminal_message(non_terminal) is False


def test_dispatch_a2a_request_requires_on_message_callback():
    channel = build_channel()

    async def _run():
        await channel.dispatch_a2a_request(
            request_id="req-1",
            session_id="sess-1",
            query="hello",
        )

    with pytest.raises(RuntimeError, match="on_message callback"):
        asyncio.run(_run())


def test_dispatch_a2a_request_and_send_queue_roundtrip():
    channel = build_channel()
    seen = []

    async def on_message(msg: Message):
        seen.append(msg)

    async def _run():
        channel.on_message(on_message)
        pending = await channel.dispatch_a2a_request(
            request_id="req-2",
            session_id="sess-2",
            query="hello",
            files=[{"filename": "x.txt", "data": "aGVsbG8="}],
            metadata={"trace_id": "t-1"},
        )

        assert len(seen) == 1
        outbound = seen[0]
        assert outbound.id == "req-2"
        assert outbound.session_id == "sess-2"
        assert outbound.params["query"] == "hello"
        assert outbound.params["files"][0]["filename"] == "x.txt"
        assert outbound.metadata == {"trace_id": "t-1"}

        inbound = Message(
            id="req-2",
            type="event",
            channel_id="a2a",
            session_id="sess-2",
            params={},
            timestamp=time.time(),
            ok=True,
            payload={"content": "ok", "is_complete": True},
            event_type=EventType.CHAT_FINAL,
        )
        await channel.send(inbound)
        queued = await pending.queue.get()
        assert queued.payload["content"] == "ok"

    asyncio.run(_run())
