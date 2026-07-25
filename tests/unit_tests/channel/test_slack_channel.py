"""Unit tests for the Slack Socket Mode channel."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from jiuwenswarm.common.schema.message import EventType, Message
from jiuwenswarm.gateway.channel_manager.base import RobotMessageRouter
from jiuwenswarm.gateway.channel_manager.im_platforms.slack import slack_connect
from jiuwenswarm.gateway.channel_manager.im_platforms.slack.slack_connect import (
    SlackChannel,
    SlackChannelConfig,
)
from jiuwenswarm.gateway.routing.keys import SlackDeliveryTarget, make_delivery_target
from jiuwenswarm.gateway.routing.session_sharing import RoutingTarget


def _message(
    *,
    event_type: EventType = EventType.CHAT_FINAL,
    content: str = "response",
    metadata: dict[str, Any] | None = None,
    session_id: str = "slack_T1_C1_1710000000.000100",
) -> Message:
    return Message(
        id="response-1",
        type="event",
        channel_id="slack",
        session_id=session_id,
        params={},
        timestamp=time.time(),
        ok=True,
        payload={"content": content},
        event_type=event_type,
        metadata=metadata,
    )


@pytest.mark.asyncio
async def test_app_mention_creates_thread_scoped_message_and_deduplicates() -> None:
    channel = SlackChannel(
        SlackChannelConfig(
            enabled=True,
            allow_from=["U1"],
            allowed_channel_ids=["C1"],
            reply_in_thread=True,
        ),
        RobotMessageRouter(),
    )
    channel._running = True
    received: list[Message] = []
    channel.on_message(received.append)

    event = {
        "type": "app_mention",
        "user": "U1",
        "channel": "C1",
        "channel_type": "channel",
        "text": "<@U-BOT> summarize this",
        "ts": "1710000000.000100",
    }
    body = {
        "event_id": "Ev1",
        "team_id": "T1",
        "authorizations": [{"user_id": "U-BOT", "is_bot": True}],
    }

    await channel._handle_app_mention(event, body)
    await channel._handle_app_mention(event, body)

    assert len(received) == 1
    message = received[0]
    assert message.params == {"content": "summarize this", "query": "summarize this"}
    assert message.session_id == "slack_T1_C1_1710000000.000100"
    assert message.chat_id == "C1"
    assert message.user_id == "U1"
    assert message.metadata == {
        "user_id": "U1",
        "slack_event_id": "Ev1",
        "slack_team_id": "T1",
        "slack_channel_id": "C1",
        "slack_channel_type": "channel",
        "slack_user_id": "U1",
        "slack_message_ts": "1710000000.000100",
        "slack_thread_ts": "1710000000.000100",
    }


@pytest.mark.asyncio
async def test_direct_message_is_not_restricted_by_channel_allowlist() -> None:
    channel = SlackChannel(
        SlackChannelConfig(
            enabled=True,
            allow_from=["U1"],
            allowed_channel_ids=["C-ONLY"],
        ),
        RobotMessageRouter(),
    )
    channel._running = True
    received: list[Message] = []
    channel.on_message(received.append)

    await channel._handle_message_event(
        {
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "user": "U1",
            "text": "hello",
            "ts": "1710000001.000200",
        },
        {"event_id": "Ev2", "team_id": "T1"},
    )

    assert len(received) == 1
    assert received[0].session_id == "slack_T1_D1_U1"
    assert received[0].metadata["slack_thread_ts"] == ""


@pytest.mark.asyncio
async def test_auto_link_channel_processes_links_with_optional_prompt() -> None:
    channel = SlackChannel(
        SlackChannelConfig(
            enabled=True,
            allow_from=["U1"],
            allowed_channel_ids=["C-MENTIONS"],
            auto_link_channel_ids=["C-RESEARCH"],
            auto_link_prompt="Review this link using the configured workflow.",
            reply_in_thread=True,
        ),
        RobotMessageRouter(),
    )
    channel._running = True
    received: list[Message] = []
    channel.on_message(received.append)

    await channel._handle_message_event(
        {
            "type": "message",
            "channel_type": "channel",
            "channel": "C-RESEARCH",
            "user": "U1",
            "text": "plain text is ignored",
            "ts": "1710000002.000100",
        },
        {"event_id": "EvAutoPlain", "team_id": "T1"},
    )
    await channel._handle_message_event(
        {
            "type": "message",
            "channel_type": "channel",
            "channel": "C-OTHER",
            "user": "U1",
            "text": "https://example.com/ignored",
            "ts": "1710000002.000200",
        },
        {"event_id": "EvAutoOther", "team_id": "T1"},
    )
    await channel._handle_message_event(
        {
            "type": "message",
            "channel_type": "channel",
            "channel": "C-RESEARCH",
            "user": "U1",
            "text": "Please analyze <https://arxiv.org/abs/2401.00001|this paper>",
            "ts": "1710000002.000300",
        },
        {"event_id": "EvAutoLink", "team_id": "T1"},
    )

    assert len(received) == 1
    message = received[0]
    assert message.params["content"] == (
        "Please analyze <https://arxiv.org/abs/2401.00001|this paper>\n\n"
        "Review this link using the configured workflow."
    )
    assert message.params["query"] == message.params["content"]
    assert message.session_id == "slack_T1_C-RESEARCH_1710000002.000300"
    assert message.metadata["slack_trigger"] == "auto_link"
    assert message.metadata["slack_thread_ts"] == "1710000002.000300"


@pytest.mark.asyncio
async def test_auto_link_channel_preserves_message_without_a_prompt() -> None:
    channel = SlackChannel(
        SlackChannelConfig(
            enabled=True,
            auto_link_channel_ids=["C-RESEARCH"],
        ),
        RobotMessageRouter(),
    )
    channel._running = True
    received: list[Message] = []
    channel.on_message(received.append)

    await channel._handle_message_event(
        {
            "type": "message",
            "channel_type": "channel",
            "channel": "C-RESEARCH",
            "user": "U1",
            "text": "https://example.com/release-notes",
            "ts": "1710000002.000350",
        },
        {"event_id": "EvAutoNoPrompt", "team_id": "T1"},
    )

    assert len(received) == 1
    assert received[0].params["content"] == "https://example.com/release-notes"


@pytest.mark.asyncio
async def test_auto_link_channel_ignores_leading_bot_mentions() -> None:
    channel = SlackChannel(
        SlackChannelConfig(
            enabled=True,
            auto_link_channel_ids=["C-RESEARCH"],
        ),
        RobotMessageRouter(),
    )
    channel._running = True
    received: list[Message] = []
    channel.on_message(received.append)

    event = {
        "type": "message",
        "channel_type": "channel",
        "channel": "C-RESEARCH",
        "user": "U1",
        "text": "<@U-BOT> analyze https://example.com/paper",
        "ts": "1710000002.000400",
    }
    await channel._handle_message_event(
        event,
        {
            "event_id": "EvAutoMention",
            "team_id": "T1",
            "authorizations": [{"user_id": "U-BOT", "is_bot": True}],
        },
    )

    assert received == []


@pytest.mark.asyncio
async def test_auto_link_channel_processes_links_with_other_user_mention() -> None:
    channel = SlackChannel(
        SlackChannelConfig(
            enabled=True,
            auto_link_channel_ids=["C-RESEARCH"],
        ),
        RobotMessageRouter(),
    )
    channel._running = True
    received: list[Message] = []
    channel.on_message(received.append)

    await channel._handle_message_event(
        {
            "type": "message",
            "channel_type": "channel",
            "channel": "C-RESEARCH",
            "user": "U1",
            "text": "<@U2> review https://example.com/paper",
            "ts": "1710000002.000450",
        },
        {
            "event_id": "EvAutoUserMention",
            "team_id": "T1",
            "authorizations": [{"user_id": "U-BOT", "is_bot": True}],
        },
    )

    assert len(received) == 1
    assert received[0].params["content"] == "<@U2> review https://example.com/paper"


@pytest.mark.asyncio
async def test_event_filters_reject_bots_subtypes_users_and_channels() -> None:
    channel = SlackChannel(
        SlackChannelConfig(
            enabled=True,
            allow_from=["U1"],
            allowed_channel_ids=["C1"],
        ),
        RobotMessageRouter(),
    )
    channel._running = True
    received: list[Message] = []
    channel.on_message(received.append)
    base_event = {
        "type": "app_mention",
        "user": "U1",
        "channel": "C1",
        "text": "<@B1> hello",
        "ts": "1710000002.000300",
    }

    await channel._handle_app_mention(
        {**base_event, "bot_id": "B1"}, {"event_id": "Ev3", "team_id": "T1"}
    )
    await channel._handle_app_mention(
        {**base_event, "subtype": "message_changed"},
        {"event_id": "Ev4", "team_id": "T1"},
    )
    await channel._handle_app_mention(
        {**base_event, "user": "U2"},
        {"event_id": "Ev5", "team_id": "T1"},
    )
    await channel._handle_app_mention(
        {**base_event, "channel": "C2"},
        {"event_id": "Ev6", "team_id": "T1"},
    )

    assert received == []


class _FakeSlackClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.auth_test_calls = 0

    async def chat_postMessage(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    async def auth_test(self) -> dict[str, str]:
        self.auth_test_calls += 1
        return {"user_id": "U-BOT"}


@pytest.mark.asyncio
async def test_auth_test_failure_does_not_block_bot_identity_loading() -> None:
    class FailingSlackClient:
        async def auth_test(self) -> dict[str, str]:
            raise RuntimeError("Slack unavailable")

    channel = SlackChannel(SlackChannelConfig(enabled=True), RobotMessageRouter())
    channel._client = FailingSlackClient()

    await channel._load_bot_user_id()

    assert channel._bot_user_id == ""


@pytest.mark.asyncio
async def test_missing_bot_identity_still_deduplicates_message_and_mention() -> None:
    channel = SlackChannel(
        SlackChannelConfig(
            enabled=True,
            auto_link_channel_ids=["C-RESEARCH"],
        ),
        RobotMessageRouter(),
    )
    channel._running = True
    received: list[Message] = []
    channel.on_message(received.append)

    shared_event = {
        "user": "U1",
        "channel": "C-RESEARCH",
        "channel_type": "channel",
        "text": "<@U-BOT> analyze https://example.com/paper",
        "ts": "1710000002.000475",
    }
    await channel._handle_message_event(
        {
            **shared_event,
            "type": "message",
            "client_msg_id": "MsgAutoMention",
        },
        {"event_id": "EvAutoMentionMessage", "team_id": "T1"},
    )
    await channel._handle_app_mention(
        {**shared_event, "type": "app_mention"},
        {"event_id": "EvAutoMentionApp", "team_id": "T1"},
    )

    assert channel._bot_user_id == ""
    assert len(received) == 1


@pytest.mark.asyncio
async def test_acknowledgement_is_sent_once_before_agent_processing() -> None:
    channel = SlackChannel(
        SlackChannelConfig(
            enabled=True,
            allow_from=["U1"],
            allowed_channel_ids=["C1"],
            reply_in_thread=True,
            acknowledge_requests=True,
            acknowledgement_text="Received. Analyzing…",
        ),
        RobotMessageRouter(),
    )
    client = _FakeSlackClient()
    channel._running = True
    channel._client = client
    calls_seen_by_agent: list[list[dict[str, Any]]] = []
    channel.on_message(lambda _: calls_seen_by_agent.append(list(client.calls)))

    event = {
        "type": "app_mention",
        "user": "U1",
        "channel": "C1",
        "channel_type": "channel",
        "text": "<@B1> analyze this",
        "ts": "1710000002.000500",
    }
    body = {"event_id": "EvAck", "team_id": "T1"}

    await channel._handle_app_mention(event, body)
    await channel._handle_app_mention(event, body)

    expected_call = {
        "channel": "C1",
        "text": "Received. Analyzing…",
        "thread_ts": "1710000002.000500",
    }
    assert client.calls == [expected_call]
    assert calls_seen_by_agent == [[expected_call]]


@pytest.mark.asyncio
async def test_acknowledgement_failure_does_not_block_agent_processing() -> None:
    class FailingSlackClient:
        async def chat_postMessage(self, **kwargs: Any) -> None:
            raise RuntimeError("Slack unavailable")

    channel = SlackChannel(
        SlackChannelConfig(
            enabled=True,
            allow_from=["U1"],
            acknowledge_requests=True,
        ),
        RobotMessageRouter(),
    )
    channel._running = True
    channel._client = FailingSlackClient()
    received: list[Message] = []
    channel.on_message(received.append)

    await channel._handle_message_event(
        {
            "type": "message",
            "channel_type": "im",
            "channel": "D1",
            "user": "U1",
            "text": "analyze this",
            "ts": "1710000002.000600",
        },
        {"event_id": "EvAckFailure", "team_id": "T1"},
    )

    assert len(received) == 1


@pytest.mark.asyncio
async def test_send_uses_routing_target_chunks_text_and_ignores_delta() -> None:
    channel = SlackChannel(SlackChannelConfig(enabled=True), RobotMessageRouter())
    client = _FakeSlackClient()
    channel._client = client
    target = RoutingTarget(
        intent="godview",
        delivery=SlackDeliveryTarget(
            target_channel_id="C-TARGET",
            thread_ts="1710000003.000400",
        ),
    )

    await channel.send(_message(content="x" * 4100), routing_target=target)
    await channel.send(
        _message(event_type=EventType.CHAT_DELTA, content="partial"),
        routing_target=target,
    )

    assert len(client.calls) == 2
    assert client.calls[0] == {
        "channel": "C-TARGET",
        "text": "x" * 4000,
        "thread_ts": "1710000003.000400",
    }
    assert client.calls[1] == {
        "channel": "C-TARGET",
        "text": "x" * 100,
        "thread_ts": "1710000003.000400",
    }


@pytest.mark.asyncio
async def test_send_falls_back_to_metadata_session_and_default_channel() -> None:
    channel = SlackChannel(
        SlackChannelConfig(enabled=True, default_channel_id="C-DEFAULT"),
        RobotMessageRouter(),
    )
    client = _FakeSlackClient()
    channel._client = client

    await channel.send(
        _message(
            metadata={
                "slack_channel_id": "C-META",
                "slack_thread_ts": "1710000004.000500",
            },
        ),
    )
    await channel.send(
        _message(metadata={}, session_id="slack_T1_C-SESSION_1710000005.000600")
    )
    await channel.send(_message(metadata={}, session_id="unknown"))

    assert [call["channel"] for call in client.calls] == [
        "C-META",
        "C-SESSION",
        "C-DEFAULT",
    ]
    assert client.calls[0]["thread_ts"] == "1710000004.000500"
    assert client.calls[1]["thread_ts"] == "1710000005.000600"
    assert "thread_ts" not in client.calls[2]


@pytest.mark.asyncio
async def test_start_and_stop_socket_mode_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    closed = asyncio.Event()
    registered_events: list[str] = []
    fake_client = _FakeSlackClient()

    class FakeAsyncApp:
        def __init__(self, token: str) -> None:
            assert token == "xoxb-test"
            self.client = fake_client

        def event(self, event_name: str):
            registered_events.append(event_name)

            def register(listener):
                return listener

            return register

    class FakeSocketModeHandler:
        def __init__(self, app: Any, app_token: str) -> None:
            assert isinstance(app, FakeAsyncApp)
            assert app_token == "xapp-test"

        async def start_async(self) -> None:
            started.set()
            await closed.wait()

        async def close_async(self) -> None:
            closed.set()

    monkeypatch.setattr(slack_connect, "SLACK_AVAILABLE", True)
    monkeypatch.setattr(slack_connect, "AsyncApp", FakeAsyncApp)
    monkeypatch.setattr(slack_connect, "AsyncSocketModeHandler", FakeSocketModeHandler)

    channel = SlackChannel(
        SlackChannelConfig(
            enabled=True,
            bot_token="xoxb-test",
            app_token="xapp-test",
        ),
        RobotMessageRouter(),
    )
    task = asyncio.create_task(channel.start())
    await asyncio.wait_for(started.wait(), timeout=1)

    assert channel.is_running
    assert registered_events == ["app_mention", "message"]
    assert fake_client.auth_test_calls == 1
    assert channel._bot_user_id == "U-BOT"

    await channel.stop()
    await asyncio.wait_for(task, timeout=1)

    assert not channel.is_running
    assert closed.is_set()
    assert channel._bot_user_id == ""


def test_make_delivery_target_builds_slack_thread_target() -> None:
    target = make_delivery_target(
        "slack",
        chat_id="C1",
        physical_user_id="U1",
        thread_ts="1710000006.000700",
    )

    assert isinstance(target, SlackDeliveryTarget)
    assert target.target_channel_id == "C1"
    assert target.thread_ts == "1710000006.000700"
    assert target.physical_user_id == "U1"
    assert target.get_container_id() == "C1:1710000006.000700"
