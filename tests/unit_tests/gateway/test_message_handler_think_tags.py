import pytest

from jiuwenswarm.common.schema.message import EventType, Message

pytest.importorskip("openjiuwen")

from jiuwenswarm.gateway.message_handler.message_handler import MessageHandler


class _FakeAgentClient:
    pass


def _new_handler() -> MessageHandler:
    MessageHandler._instance = None
    return MessageHandler(_FakeAgentClient())


def _chat_msg(content: str, *, request_id: str = "req-1", event_type: EventType = EventType.CHAT_DELTA) -> Message:
    return Message(
        id=request_id,
        type="event",
        channel_id="web",
        session_id="sess-1",
        params={},
        timestamp=1.0,
        ok=True,
        payload={"event_type": event_type.value, "content": content},
        event_type=event_type,
    )


async def _drain(handler: MessageHandler) -> list[Message]:
    out = []
    while True:
        msg = await handler.consume_robot_messages(timeout=0)
        if msg is None:
            return out
        out.append(msg)


@pytest.mark.asyncio
async def test_publish_robot_messages_splits_complete_think_block():
    handler = _new_handler()

    await handler.publish_robot_messages(
        _chat_msg("<think>hidden</think>visible", event_type=EventType.CHAT_FINAL)
    )

    out = await _drain(handler)
    assert [(msg.event_type, msg.payload.get("content")) for msg in out] == [
        (EventType.CHAT_REASONING, "hidden"),
        (EventType.CHAT_FINAL, "visible"),
    ]
    assert out[0].payload.get("source_chunk_type") == "llm_reasoning"


@pytest.mark.asyncio
async def test_publish_robot_messages_splits_streamed_partial_think_tags():
    handler = _new_handler()

    await handler.publish_robot_messages(_chat_msg("answer <thi"))
    await handler.publish_robot_messages(_chat_msg("nk>hidden</thi"))
    await handler.publish_robot_messages(_chat_msg("nk> visible"))

    out = await _drain(handler)
    assert [(msg.event_type, msg.payload.get("content")) for msg in out] == [
        (EventType.CHAT_DELTA, "answer "),
        (EventType.CHAT_REASONING, "hidden"),
        (EventType.CHAT_DELTA, " visible"),
    ]
    assert all("<think" not in str(msg.payload.get("content", "")).lower() for msg in out)
    assert all("</think>" not in str(msg.payload.get("content", "")).lower() for msg in out)


@pytest.mark.asyncio
async def test_publish_robot_messages_flushes_pending_text_before_empty_final():
    handler = _new_handler()

    await handler.publish_robot_messages(_chat_msg("answer <"))
    await handler.publish_robot_messages(_chat_msg("", event_type=EventType.CHAT_FINAL))

    out = await _drain(handler)
    assert [(msg.event_type, msg.payload.get("content")) for msg in out] == [
        (EventType.CHAT_DELTA, "answer "),
        (EventType.CHAT_DELTA, "<"),
        (EventType.CHAT_FINAL, ""),
    ]
