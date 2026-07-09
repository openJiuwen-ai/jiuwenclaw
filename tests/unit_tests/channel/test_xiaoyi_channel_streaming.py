import json
import time
from typing import Any

import pytest

from jiuwenswarm.common.schema.message import EventType, Message
from jiuwenswarm.gateway.channel_manager.base import RobotMessageRouter
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_connect import (
    XiaoyiChannel,
    XiaoyiChannelConfig,
)
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_utils.formatter import (
    build_status_update_response,
    should_send_as_status_update,
)


def _build_channel(*, enable_streaming: bool = True) -> tuple[XiaoyiChannel, list[dict[str, Any]]]:
    channel = XiaoyiChannel(
        XiaoyiChannelConfig(
            agent_id="agent-1",
            enable_streaming=enable_streaming,
        ),
        RobotMessageRouter(),
    )
    sent: list[dict[str, Any]] = []

    async def fake_safe_ws_send(url_key: str, payload: dict[str, Any]) -> None:
        assert url_key == "ws_url1"
        sent.append(payload)

    channel._ws_connections = {"ws_url1": object()}
    channel._safe_ws_send = fake_safe_ws_send
    return channel, sent


def _message(event_type: EventType, payload: dict[str, Any]) -> Message:
    return Message(
        id="request-1",
        type="event",
        channel_id="xiaoyi",
        session_id="jiuwen-session-1",
        params={},
        timestamp=time.time(),
        ok=True,
        payload=payload,
        event_type=event_type,
        metadata={
            "xiaoyi_session_id": "xiaoyi-session-1",
            "xiaoyi_task_id": "xiaoyi-task-1",
        },
    )


def _result(wrapper: dict[str, Any]) -> dict[str, Any]:
    return json.loads(wrapper["msgDetail"])["result"]


def test_processing_status_is_a_status_update() -> None:
    assert should_send_as_status_update(EventType.CHAT_PROCESSING_STATUS)
    assert build_status_update_response("task-1", "working", "working")["final"] is False
    assert build_status_update_response("task-1", "done", "completed")["final"] is True
    assert build_status_update_response("task-1", "failed", "failed")["final"] is True
    assert build_status_update_response("task-1", "cancelled", "canceled")["final"] is True


@pytest.mark.asyncio
async def test_streaming_final_text_is_sent_as_terminal_artifact() -> None:
    channel, sent = _build_channel()
    summary = "抖音已经帮你打开了"
    channel._mark_session_active("xiaoyi-session-1")

    await channel.send(
        _message(
            EventType.CHAT_FINAL,
            {"event_type": "chat.final", "content": summary},
        )
    )
    await channel.send(
        _message(
            EventType.CHAT_PROCESSING_STATUS,
            {
                "event_type": "chat.processing_status",
                "is_processing": False,
                "is_complete": True,
            },
        )
    )

    assert len(sent) == 1
    artifact = _result(sent[0])
    assert artifact["kind"] == "artifact-update"
    assert artifact["append"] is False
    assert artifact["lastChunk"] is True
    assert artifact["final"] is True
    assert artifact["artifact"]["parts"] == [{"kind": "text", "text": summary}]


@pytest.mark.asyncio
async def test_processing_started_is_non_terminal_status_update() -> None:
    channel, sent = _build_channel()

    await channel.send(
        _message(
            EventType.CHAT_PROCESSING_STATUS,
            {
                "event_type": "chat.processing_status",
                "is_processing": True,
                "is_complete": False,
            },
        )
    )

    assert len(sent) == 1
    status = _result(sent[0])
    assert status["kind"] == "status-update"
    assert status["status"]["state"] == "working"
    assert status["final"] is False


@pytest.mark.asyncio
async def test_completed_status_is_terminal_without_final_text() -> None:
    channel, sent = _build_channel()
    channel._mark_session_active("xiaoyi-session-1")

    await channel.send(
        _message(
            EventType.CHAT_PROCESSING_STATUS,
            {
                "event_type": "chat.processing_status",
                "is_processing": False,
                "is_complete": True,
            },
        )
    )

    assert len(sent) == 1
    status = _result(sent[0])
    assert status["kind"] == "status-update"
    assert status["status"]["state"] == "completed"
    assert status["final"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        EventType.CHAT_USAGE_METADATA,
        EventType.CHAT_USAGE_SUMMARY,
        EventType.CONTEXT_USAGE,
    ],
)
async def test_internal_events_do_not_create_blank_artifacts(
    event_type: EventType,
) -> None:
    channel, sent = _build_channel()

    await channel.send(
        _message(
            event_type,
            {"event_type": event_type.value, "usage": {"total_tokens": 10}},
        )
    )

    assert sent == []


@pytest.mark.asyncio
async def test_non_streaming_final_text_remains_terminal_artifact() -> None:
    channel, sent = _build_channel(enable_streaming=False)

    await channel.send(
        _message(
            EventType.CHAT_FINAL,
            {"event_type": "chat.final", "content": "已完成"},
        )
    )

    assert len(sent) == 1
    artifact = _result(sent[0])
    assert artifact["kind"] == "artifact-update"
    assert artifact["append"] is False
    assert artifact["lastChunk"] is True
    assert artifact["final"] is True


@pytest.mark.asyncio
async def test_direct_gui_response_is_not_forwarded_as_user_message() -> None:
    channel, _ = _build_channel()
    gui_events: list[dict[str, Any]] = []
    user_messages: list[Message] = []
    channel.register_gui_agent_handler(gui_events.append)
    channel.on_message(user_messages.append)
    raw = {
        "jsonrpc": "2.0",
        "id": "xiaoyi-task-1",
        "method": "message/stream",
        "sessionId": "xiaoyi-session-1",
        "params": {
            "id": "xiaoyi-task-1",
            "sessionId": "params-session-1",
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": "xiaoyi-task-1",
                "parts": [
                    {
                        "kind": "data",
                        "data": {
                            "events": [
                                {
                                    "header": {
                                        "namespace": "ClawAgent",
                                        "name": "InvokeJarvisGUIAgentResponse",
                                    },
                                    "payload": {
                                        "isFinal": True,
                                        "streamInfo": {
                                            "streamContent": "layout analysis failed"
                                        },
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
        },
    }

    await channel._handle_raw_message(json.dumps(raw))

    assert len(gui_events) == 1
    assert user_messages == []
