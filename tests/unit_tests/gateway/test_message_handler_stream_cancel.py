"""Tests for gateway stream task cancellation before chat.send."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jiuwenavatar.common.schema import Message
from jiuwenavatar.common.schema.message import ReqMethod
from jiuwenavatar.gateway.message_handler.message_handler import MessageHandler


class _FakeAgentClient:
    sent_requests: list[object] = []

    @staticmethod
    async def send_request(env: object) -> SimpleNamespace:
        _FakeAgentClient.sent_requests.append(env)
        return SimpleNamespace(
            request_id="interrupt-1",
            channel_id="tui",
            ok=True,
            payload={"event_type": "chat.interrupt_result", "success": True},
            metadata=None,
        )

    @staticmethod
    async def send_request_stream(env: object):
        if False:
            yield env


class _TestMessageHandler(MessageHandler):
    @classmethod
    def create(cls) -> "_TestMessageHandler":
        setattr(MessageHandler, "_instance", None)
        setattr(cls, "_instance", None)
        _FakeAgentClient.sent_requests = []
        return cls(_FakeAgentClient())

    async def cancel_stream_tasks_for_channel(self, msg: Message) -> int:
        return await getattr(self, "_cancel_stream_tasks_for_channel")(msg)


def _chat_send_message(
    *,
    channel_id: str = "tui",
    session_id: str = "sess_new",
) -> Message:
    return Message(
        id="req-new",
        type="req",
        channel_id=channel_id,
        session_id=session_id,
        params={"mode": "agent.plan", "query": "hello"},
        timestamp=0.0,
        ok=True,
        req_method=ReqMethod.CHAT_SEND,
        is_stream=True,
    )


def _seed_stream_task(
    handler: _TestMessageHandler,
    *,
    rid: str,
    channel_id: str,
    session_id: str,
) -> asyncio.Task:
    async def _long_run() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(_long_run())
    getattr(handler, "_stream_tasks")[rid] = task
    getattr(handler, "_stream_channels")[rid] = channel_id
    getattr(handler, "_stream_sessions")[rid] = session_id
    getattr(handler, "_stream_modes")[rid] = "agent.plan"
    getattr(handler, "_stream_emits_processing_status")[rid] = False
    return task


@pytest.mark.asyncio
async def test_cancel_stream_tasks_only_affects_same_channel() -> None:
    handler = _TestMessageHandler.create()
    tui_task = _seed_stream_task(
        handler, rid="rid-tui", channel_id="tui", session_id="sess_old",
    )
    web_task = _seed_stream_task(
        handler, rid="rid-web", channel_id="web", session_id="sess_web",
    )

    cancelled = await handler.cancel_stream_tasks_for_channel(
        _chat_send_message(channel_id="tui", session_id="sess_new"),
    )

    assert cancelled == 1
    assert tui_task.cancelled()
    assert not web_task.cancelled()
    assert "rid-tui" not in getattr(handler, "_stream_tasks")
    assert "rid-web" in getattr(handler, "_stream_tasks")
    await asyncio.sleep(0)
    assert len(_FakeAgentClient.sent_requests) == 1


@pytest.mark.asyncio
async def test_single_user_channel_cancels_orphan_session_on_same_channel() -> None:
    handler = _TestMessageHandler.create()
    orphan_task = _seed_stream_task(
        handler, rid="rid-orphan", channel_id="tui", session_id="sess_orphan",
    )

    cancelled = await handler.cancel_stream_tasks_for_channel(
        _chat_send_message(channel_id="tui", session_id="sess_new"),
    )

    assert cancelled == 1
    assert orphan_task.cancelled()
    await asyncio.sleep(0)
    assert len(_FakeAgentClient.sent_requests) == 1


@pytest.mark.asyncio
async def test_web_channel_only_cancels_matching_session() -> None:
    handler = _TestMessageHandler.create()
    same_session_task = _seed_stream_task(
        handler, rid="rid-a", channel_id="web", session_id="sess_a",
    )
    other_session_task = _seed_stream_task(
        handler, rid="rid-b", channel_id="web", session_id="sess_b",
    )

    cancelled = await handler.cancel_stream_tasks_for_channel(
        _chat_send_message(channel_id="web", session_id="sess_a"),
    )

    assert cancelled == 1
    assert same_session_task.cancelled()
    assert not other_session_task.cancelled()
    await asyncio.sleep(0)
    assert len(_FakeAgentClient.sent_requests) == 1


@pytest.mark.asyncio
async def test_stream_without_session_id_still_notifies_agent() -> None:
    """Streams missing session metadata must still send chat.interrupt."""
    handler = _TestMessageHandler.create()
    _seed_stream_task(
        handler, rid="rid-peer", channel_id="tui", session_id="sess_resolved",
    )

    async def _long_run() -> None:
        await asyncio.sleep(3600)

    orphan_task = asyncio.create_task(_long_run())
    getattr(handler, "_stream_tasks")["rid-no-sid"] = orphan_task
    getattr(handler, "_stream_channels")["rid-no-sid"] = "tui"
    getattr(handler, "_stream_sessions")["rid-no-sid"] = None
    getattr(handler, "_stream_modes")["rid-no-sid"] = "agent.plan"
    getattr(handler, "_stream_emits_processing_status")["rid-no-sid"] = False

    cancelled = await handler.cancel_stream_tasks_for_channel(
        _chat_send_message(channel_id="tui", session_id="sess_new"),
    )

    assert cancelled == 2
    assert orphan_task.cancelled()
    await asyncio.sleep(0)
    assert len(_FakeAgentClient.sent_requests) == 1


def test_is_single_user_channel_includes_cli_alias() -> None:
    _is_single_user_channel = getattr(MessageHandler, "_is_single_user_channel")
    assert _is_single_user_channel("tui")
    assert _is_single_user_channel("acp")
    assert _is_single_user_channel("cli")
    assert not _is_single_user_channel("web")


# ── cancel_agent_sessions_on_disconnect ─────────────────────────
#
# Regression: when the user's WebSocket closes but `_session_to_client`
# was overwritten by a later reconnect with the same session_id, the
# gateway-supplied ``stale_session_keys`` ends up empty. In that case
# the disconnect handler must still recover session_id via the in-flight
# stream bookkeeping (``_stream_sessions[request_id]``).


@pytest.mark.asyncio
async def test_disconnect_recovers_session_from_stale_request_keys() -> None:
    handler = _TestMessageHandler.create()
    # In-flight stream tied to this WS via a stale request key, but
    # _session_to_client lookup yields nothing (later reconnect overwrote).
    _seed_stream_task(
        handler, rid="rid-stale", channel_id="tui", session_id="sess_live",
    )

    await handler.cancel_agent_sessions_on_disconnect(
        [],  # empty stale_session_keys (the bug we are guarding against)
        stale_request_keys=[("tui", "rid-stale")],
    )

    await asyncio.sleep(0)
    # Exactly one chat.interrupt must have been emitted for the recovered session.
    assert len(_FakeAgentClient.sent_requests) == 1


@pytest.mark.asyncio
async def test_disconnect_with_empty_inputs_is_a_noop() -> None:
    handler = _TestMessageHandler.create()
    await handler.cancel_agent_sessions_on_disconnect([], stale_request_keys=[])
    await asyncio.sleep(0)
    assert _FakeAgentClient.sent_requests == []


@pytest.mark.asyncio
async def test_disconnect_dedupes_session_across_both_sources() -> None:
    """A session present in both session_keys and request_keys must only fire once."""
    handler = _TestMessageHandler.create()
    _seed_stream_task(
        handler, rid="rid-dup", channel_id="tui", session_id="sess_dup",
    )

    await handler.cancel_agent_sessions_on_disconnect(
        [("tui", "sess_dup")],
        stale_request_keys=[("tui", "rid-dup")],
    )

    await asyncio.sleep(0)
    assert len(_FakeAgentClient.sent_requests) == 1


@pytest.mark.asyncio
async def test_disconnect_backward_compatible_without_request_keys_kwarg() -> None:
    """Existing callers that only pass session_keys must continue to work."""
    handler = _TestMessageHandler.create()
    _seed_stream_task(
        handler, rid="rid-legacy", channel_id="tui", session_id="sess_legacy",
    )

    await handler.cancel_agent_sessions_on_disconnect([("tui", "sess_legacy")])

    await asyncio.sleep(0)
    assert len(_FakeAgentClient.sent_requests) == 1