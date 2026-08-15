# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for gateway stream task cancellation before chat.send."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jiuwenswarm.common.schema import Message
from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.gateway.message_handler.message_handler import ChannelMode, MessageHandler
from jiuwenswarm.gateway.routing.session_sharing import SubRole


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


class _DisconnectingStreamAgentClient:
    @staticmethod
    async def send_request(env: object) -> SimpleNamespace:
        raise AssertionError("stream disconnect test should not call send_request")

    @staticmethod
    async def send_request_stream(env: object):
        if False:  # pragma: no cover - keeps this an async generator
            yield env
        raise RuntimeError("AgentServer WebSocket connection closed")


class _HangingAgentClient:
    @staticmethod
    async def send_request(env: object) -> SimpleNamespace:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    @staticmethod
    async def send_request_stream(env: object):
        if False:  # pragma: no cover - keeps this an async generator
            yield env


class _FailedCancelAgentClient:
    @staticmethod
    async def send_request(env: object) -> SimpleNamespace:
        return SimpleNamespace(
            request_id="interrupt-failed",
            channel_id="tui",
            ok=False,
            payload={
                "event_type": "chat.interrupt_result",
                "success": False,
                "error": "session runtime cleanup failed",
            },
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

    @classmethod
    def create_with_client(cls, client: object) -> "_TestMessageHandler":
        setattr(MessageHandler, "_instance", None)
        setattr(cls, "_instance", None)
        return cls(client)

    async def cancel_stream_tasks_for_channel(self, msg: Message) -> int:
        return await getattr(self, "_cancel_stream_tasks_for_channel")(msg)

    async def cancel_agent_work_for_session(self, msg: Message, session_id: str) -> None:
        await getattr(self, "_cancel_agent_work_for_session")(msg, session_id)


def _chat_send_message(
    *,
    channel_id: str = "tui",
    session_id: str = "sess_new",
    mode: str = "agent.plan",
) -> Message:
    return Message(
        id="req-new",
        type="req",
        channel_id=channel_id,
        session_id=session_id,
        params={"mode": mode, "query": "hello"},
        timestamp=0.0,
        ok=True,
        req_method=ReqMethod.CHAT_SEND,
        is_stream=True,
    )


def _team_transport_message(*, request_id: str, method: ReqMethod, ws_id: str) -> Message:
    return Message(
        id=request_id,
        type="req",
        channel_id="web",
        session_id="sess-godview",
        params={"mode": "team"},
        timestamp=0.0,
        ok=True,
        req_method=method,
        is_stream=method == ReqMethod.CHAT_SEND,
        metadata={"ws_id": ws_id, "user_id": "web-user"},
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


async def _drain_robot_messages(handler: _TestMessageHandler) -> list[Message]:
    messages: list[Message] = []
    while True:
        msg = await handler.consume_robot_messages(timeout=0.01)
        if msg is None:
            return messages
        messages.append(msg)


async def _wait_for_sent_request_count(count: int, timeout: float = 0.2) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while len(_FakeAgentClient.sent_requests) < count:
        if asyncio.get_running_loop().time() >= deadline:
            return
        await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_tui_non_stream_request_times_out_before_frontend_request(monkeypatch) -> None:
    handler = _TestMessageHandler.create_with_client(_HangingAgentClient())
    monkeypatch.setattr(
        "jiuwenswarm.gateway.routing.agent_request_timeout._TUI_DEFAULT_UNARY_TIMEOUT_SECONDS",
        0.01,
        raising=False,
    )
    msg = Message(
        id="tui-timeout-request",
        type="req",
        channel_id="tui",
        session_id="sess-tui-timeout",
        params={"value": "check"},
        timestamp=0.0,
        ok=True,
        req_method=ReqMethod.COMMAND_STATUS,
        is_stream=False,
    )
    env = e2a_from_agent_fields(
        request_id=msg.id,
        channel_id=msg.channel_id,
        session_id=msg.session_id,
        req_method=ReqMethod.COMMAND_STATUS,
        params=msg.params,
        is_stream=False,
        timestamp=0.0,
    )

    await asyncio.wait_for(
        handler._process_non_stream_request(msg, env),  # pylint: disable=protected-access
        timeout=0.2,
    )

    outputs = await _drain_robot_messages(handler)
    assert len(outputs) == 1
    assert outputs[0].ok is False
    assert outputs[0].payload == {
        "error": "AgentServer request timed out",
        "code": "AGENT_SERVER_TIMEOUT",
    }


@pytest.mark.asyncio
async def test_cancel_stream_tasks_only_affects_same_channel() -> None:
    handler = _TestMessageHandler.create()
    tui_task = _seed_stream_task(
        handler, rid="rid-tui", channel_id="tui", session_id="sess_old",
    )
    web_task = _seed_stream_task(
        handler, rid="rid-web", channel_id="web", session_id="sess_web",
    )

    # TUI is no longer single-user: different session on same channel does NOT cancel.
    cancelled = await handler.cancel_stream_tasks_for_channel(
        _chat_send_message(channel_id="tui", session_id="sess_new"),
    )

    assert cancelled == 0
    assert not tui_task.cancelled()
    assert not web_task.cancelled()
    assert "rid-tui" in getattr(handler, "_stream_tasks")
    assert "rid-web" in getattr(handler, "_stream_tasks")
    await asyncio.sleep(0)
    assert len(_FakeAgentClient.sent_requests) == 0


@pytest.mark.asyncio
async def test_process_stream_publishes_error_and_stops_processing_on_connection_close() -> None:
    handler = _TestMessageHandler.create_with_client(_DisconnectingStreamAgentClient())
    env = SimpleNamespace(
        request_id="rid-stream-close",
        channel="web",
        params={"content": "hello"},
    )

    await handler.process_stream(
        env,
        session_id="sess-stream-close",
        request_metadata={"source": "test"},
    )

    outputs = await _drain_robot_messages(handler)
    payloads = [msg.payload for msg in outputs]

    assert any(
        payload.get("event_type") == "chat.error"
        and "AgentServer WebSocket connection closed" in payload.get("error", "")
        for payload in payloads
        if isinstance(payload, dict)
    )
    assert any(
        payload.get("event_type") == "chat.processing_status"
        and payload.get("is_processing") is False
        for payload in payloads
        if isinstance(payload, dict)
    )


@pytest.mark.asyncio
async def test_tui_no_longer_cancels_orphan_session() -> None:
    """TUI is no longer single-user: different session does not cancel orphan stream."""
    handler = _TestMessageHandler.create()
    orphan_task = _seed_stream_task(
        handler, rid="rid-orphan", channel_id="tui", session_id="sess_orphan",
    )

    cancelled = await handler.cancel_stream_tasks_for_channel(
        _chat_send_message(channel_id="tui", session_id="sess_new"),
    )

    assert cancelled == 0
    assert not orphan_task.cancelled()
    await asyncio.sleep(0)
    assert len(_FakeAgentClient.sent_requests) == 0


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
async def test_tui_keeps_streams_on_different_session() -> None:
    """TUI is no longer single-user: different session preserves all in-flight streams."""
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

    assert cancelled == 0
    assert not orphan_task.cancelled()
    await asyncio.sleep(0)
    assert len(_FakeAgentClient.sent_requests) == 0


def test_is_single_user_channel_acp_only() -> None:
    _is_single_user_channel = getattr(MessageHandler, "_is_single_user_channel")
    assert not _is_single_user_channel("tui")
    assert _is_single_user_channel("acp")
    assert not _is_single_user_channel("cli")
    assert not _is_single_user_channel("web")


def test_team_chat_send_keeps_existing_team_stream() -> None:
    _should_cancel_existing_stream_before_chat_send = getattr(
        MessageHandler,
        "_should_cancel_existing_stream_before_chat_send",
    )

    assert not _should_cancel_existing_stream_before_chat_send(
        _chat_send_message(channel_id="web", session_id="sess_team", mode="team"),
    )
    assert not _should_cancel_existing_stream_before_chat_send(
        _chat_send_message(channel_id="web", session_id="sess_team", mode="code.team"),
    )
    assert not _should_cancel_existing_stream_before_chat_send(
        _chat_send_message(channel_id="web", session_id="sess_team", mode="team.plan"),
    )
    assert _should_cancel_existing_stream_before_chat_send(
        _chat_send_message(channel_id="web", session_id="sess_agent", mode="agent.plan"),
    )


def test_ask_user_answer_chat_send_keeps_existing_stream() -> None:
    _should_cancel_existing_stream_before_chat_send = getattr(
        MessageHandler,
        "_should_cancel_existing_stream_before_chat_send",
    )
    msg = _chat_send_message(
        channel_id="tui",
        session_id="sess_team",
        mode="team.plan",
    )
    msg.params.update(
        {
            "query": "",
            "source": "ask_user_interrupt",
            "request_id": "call_ask_1",
            "answers": [
                {
                    "question": "你希望用什么技术实现？",
                    "selected_options": ["浏览器（HTML/CSS/JS）"],
                }
            ],
        }
    )

    assert not _should_cancel_existing_stream_before_chat_send(msg)


def test_confirm_interrupt_answer_chat_send_keeps_existing_stream() -> None:
    _should_cancel_existing_stream_before_chat_send = getattr(
        MessageHandler,
        "_should_cancel_existing_stream_before_chat_send",
    )
    msg = _chat_send_message(
        channel_id="tui",
        session_id="sess_team",
        mode="team.plan",
    )
    msg.params.update(
        {
            "query": "",
            "source": "confirm_interrupt",
            "request_id": "call_confirm_1",
            "answers": [{"selected_options": ["批准"], "custom_input": ""}],
            "plan_approval_kind": "plan_approval",
            "plan_content": "# 团队计划",
            "plan_language": "cn",
        }
    )

    assert not _should_cancel_existing_stream_before_chat_send(msg)


def test_raw_goal_text_is_not_interaction_managed_without_explicit_mode() -> None:
    _is_interaction_managed_chat_send = getattr(
        MessageHandler,
        "_is_interaction_managed_chat_send",
    )

    assert _is_interaction_managed_chat_send(
        _chat_send_message(session_id="sess-goal", mode="agent.plan")
    ) is False

    goal_msg = _chat_send_message(session_id="sess-goal", mode="agent.plan")
    goal_msg.params["query"] = "/goal set 完成项目重构"
    assert _is_interaction_managed_chat_send(goal_msg) is False

    objective_msg = _chat_send_message(session_id="sess-goal", mode="agent.plan")
    objective_msg.params["query"] = "/goal 完成项目重构"
    assert _is_interaction_managed_chat_send(objective_msg) is False

    resume_msg = _chat_send_message(session_id="sess-goal", mode="agent.plan")
    resume_msg.params["query"] = "/goal resume"
    assert _is_interaction_managed_chat_send(resume_msg) is False

    pause_msg = _chat_send_message(session_id="sess-goal", mode="agent.plan")
    pause_msg.params["query"] = "/goal pause"
    assert _is_interaction_managed_chat_send(pause_msg) is False

    clear_msg = _chat_send_message(session_id="sess-goal", mode="agent.plan")
    clear_msg.params["query"] = "/goal clear"
    assert _is_interaction_managed_chat_send(clear_msg) is False

    attach_msg = _chat_send_message(session_id="sess-goal", mode="agent")
    attach_msg.params["query"] = ""
    attach_msg.params["attach_goal"] = True
    assert _is_interaction_managed_chat_send(attach_msg) is True

    steer_msg = _chat_send_message(session_id="sess-goal", mode="agent")
    steer_msg.params["input_mode"] = "steer"
    assert _is_interaction_managed_chat_send(steer_msg) is True

    follow_up_msg = _chat_send_message(session_id="sess-goal", mode="agent")
    follow_up_msg.params["input_mode"] = "follow_up"
    assert _is_interaction_managed_chat_send(follow_up_msg) is True

    runtime_mode_msg = _chat_send_message(session_id="sess-goal", mode="agent")
    runtime_mode_msg.params["runtime_mode"] = "steer"
    assert _is_interaction_managed_chat_send(runtime_mode_msg) is True


def test_goal_attach_chat_send_does_not_cancel_existing_user_stream() -> None:
    _should_cancel_existing_stream_before_chat_send = getattr(
        MessageHandler,
        "_should_cancel_existing_stream_before_chat_send",
    )
    # Plain ``/goal set`` text without input_mode still replaces (pre-Goal lifecycle).
    plain_goal_msg = _chat_send_message(channel_id="tui", session_id="sess-goal")
    plain_goal_msg.params["query"] = "/goal set 查询石家庄天气"
    assert _should_cancel_existing_stream_before_chat_send(plain_goal_msg) is True

    # Second-step attach must keep the existing host stream.
    attach_msg = _chat_send_message(channel_id="tui", session_id="sess-goal")
    attach_msg.params["query"] = ""
    attach_msg.params["attach_goal"] = True
    assert _should_cancel_existing_stream_before_chat_send(attach_msg) is False


@pytest.mark.asyncio
async def test_interaction_managed_goal_input_does_not_cancel_existing_stream() -> None:
    handler = _TestMessageHandler.create()
    goal_task = _seed_stream_task(
        handler, rid="rid-goal", channel_id="web", session_id="sess_goal",
    )
    msg = _chat_send_message(channel_id="web", session_id="sess_goal")
    msg.params["input_mode"] = "steer"

    assert not handler._should_cancel_existing_stream_before_chat_send(msg)
    assert not goal_task.cancelled()
    await asyncio.sleep(0)
    assert len(_FakeAgentClient.sent_requests) == 0


@pytest.mark.asyncio
async def test_plain_chat_send_still_replaces_existing_stream() -> None:
    handler = _TestMessageHandler.create()
    goal_task = _seed_stream_task(
        handler, rid="rid-goal", channel_id="tui", session_id="sess_goal",
    )
    msg = _chat_send_message(channel_id="tui", session_id="sess_goal")
    msg.params["query"] = "also check Tianjin weather"

    try:
        cancelled = await handler.cancel_stream_tasks_for_channel(msg)
        assert cancelled == 1
        assert goal_task.cancelled()
    finally:
        if not goal_task.done():
            goal_task.cancel()
        await asyncio.gather(goal_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_follow_up_input_keeps_existing_stream() -> None:
    handler = _TestMessageHandler.create()
    goal_task = _seed_stream_task(
        handler, rid="rid-goal", channel_id="tui", session_id="sess_goal",
    )
    msg = _chat_send_message(channel_id="tui", session_id="sess_goal")
    msg.params["runtime_mode"] = "follow_up"

    try:
        assert not handler._should_cancel_existing_stream_before_chat_send(msg)
        assert not goal_task.cancelled()
    finally:
        goal_task.cancel()
        await asyncio.gather(goal_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_steer_input_keeps_existing_stream() -> None:
    handler = _TestMessageHandler.create()
    goal_task = _seed_stream_task(
        handler, rid="rid-goal", channel_id="web", session_id="sess_goal",
    )
    msg = _chat_send_message(channel_id="web", session_id="sess_goal")
    msg.params["input_mode"] = "steer"

    try:
        assert not handler._should_cancel_existing_stream_before_chat_send(msg)
        assert not goal_task.cancelled()
    finally:
        goal_task.cancel()
        await asyncio.gather(goal_task, return_exceptions=True)


def test_permission_interrupt_answer_chat_send_keeps_existing_stream() -> None:
    _should_cancel_existing_stream_before_chat_send = getattr(
        MessageHandler,
        "_should_cancel_existing_stream_before_chat_send",
    )
    msg = _chat_send_message(
        channel_id="tui",
        session_id="sess_perm",
        mode="code.plan",
    )
    msg.params.update(
        {
            "query": "",
            "source": "permission_interrupt",
            "request_id": "call_perm_1",
            "answers": [{"selected_options": ["allow_once"], "custom_input": ""}],
        }
    )

    assert not _should_cancel_existing_stream_before_chat_send(msg)


@pytest.mark.parametrize(
    "params",
    [
        {
            "query": "",
            "source": "evolution_interrupt",
            "request_id": "call_evolve_1",
            "answers": [{"selected_options": ["allow_always"], "custom_input": ""}],
            "approval_kind": "evolve",
        },
        {
            "query": "",
            "source": "skill_evolution_approval",
            "request_id": "call_evolve_1",
            "answers": [{"selected_options": ["allow_always"], "custom_input": ""}],
            "approval_schema": "openjiuwen.skill_evolution_approval.v1",
            "evolution_meta": {
                "event_kind": "approval",
                "rail_kind": "regular",
                "approval_kind": "evolve",
                "approval_transport": "interrupt",
            },
        },
    ],
)
def test_evolution_interrupt_answer_chat_send_keeps_existing_stream(params) -> None:
    _should_cancel_existing_stream_before_chat_send = getattr(
        MessageHandler,
        "_should_cancel_existing_stream_before_chat_send",
    )
    msg = _chat_send_message(
        channel_id="web",
        session_id="sess_evolve",
        mode="agent.plan",
    )
    msg.params.update(params)

    assert not _should_cancel_existing_stream_before_chat_send(msg)


def test_passive_evolution_approval_chat_send_still_cancels_existing_stream() -> None:
    _should_cancel_existing_stream_before_chat_send = getattr(
        MessageHandler,
        "_should_cancel_existing_stream_before_chat_send",
    )
    msg = _chat_send_message(
        channel_id="web",
        session_id="sess_evolve",
        mode="agent.plan",
    )
    msg.params.update(
        {
            "query": "",
            "source": "skill_evolution_approval",
            "request_id": "regular_evolve_1",
            "answers": [{"selected_options": ["allow_always"], "custom_input": ""}],
            "approval_schema": "openjiuwen.skill_evolution_approval.v1",
            "evolution_meta": {
                "event_kind": "approval",
                "rail_kind": "regular",
                "approval_kind": "evolve",
            },
        }
    )

    assert _should_cancel_existing_stream_before_chat_send(msg)


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


def test_disconnect_accepts_agent_ref_scoped_route_keys() -> None:
    handler = _TestMessageHandler.create()
    handler._stream_sessions["rid-scoped"] = "sess_request"

    merged, recovered = handler._merge_disconnect_session_keys(
        [("tui", "sess_direct", "team:default")],
        stale_request_keys=[("tui", "rid-scoped", "team:default")],
    )

    assert merged == [("tui", "sess_direct"), ("tui", "sess_request")]
    assert recovered == [("tui", "sess_request")]


@pytest.mark.asyncio
async def test_disconnect_cancel_marks_request_as_client_disconnect() -> None:
    handler = _TestMessageHandler.create()
    _seed_stream_task(
        handler, rid="rid-disconnect", channel_id="tui", session_id="sess_exit",
    )

    await handler.cancel_agent_sessions_on_disconnect(
        [],
        stale_request_keys=[("tui", "rid-disconnect")],
    )

    assert len(_FakeAgentClient.sent_requests) == 1
    assert _FakeAgentClient.sent_requests[0].channel_context["_jiuwenswarm_cancel_source"] == "client_disconnect"
    assert "cancel_source" not in _FakeAgentClient.sent_requests[0].params


@pytest.mark.asyncio
async def test_disconnect_cancel_reports_agent_cleanup_failure() -> None:
    handler = _TestMessageHandler.create_with_client(_FailedCancelAgentClient())

    cleaned = await handler.cancel_agent_sessions_on_disconnect(
        [("tui", "sess_cleanup_failed")],
    )

    assert cleaned is False


@pytest.mark.asyncio
async def test_manual_cancel_does_not_forward_client_disconnect_source() -> None:
    handler = _TestMessageHandler.create()
    msg = Message(
        id="manual-cancel",
        type="req",
        channel_id="tui",
        session_id="sess_manual",
        params={
            "intent": "cancel",
            "session_id": "sess_manual",
            "cancel_source": "client_disconnect",
        },
        timestamp=0.0,
        ok=True,
        req_method=ReqMethod.CHAT_CANCEL,
        is_stream=False,
        metadata={"_jiuwenswarm_cancel_source": "client_disconnect"},
    )

    await handler.cancel_agent_work_for_session(msg, "sess_manual")

    assert len(_FakeAgentClient.sent_requests) == 1
    assert "cancel_source" not in _FakeAgentClient.sent_requests[0].params
    assert "_jiuwenswarm_cancel_source" not in _FakeAgentClient.sent_requests[0].channel_context


@pytest.mark.asyncio
async def test_disconnect_cancel_can_be_delayed_until_grace_expires() -> None:
    handler = _TestMessageHandler.create()
    _seed_stream_task(
        handler, rid="rid-delayed", channel_id="tui", session_id="sess_delayed",
    )

    await handler.schedule_cancel_agent_sessions_on_disconnect(
        [],
        stale_request_keys=[("tui", "rid-delayed")],
        delay_seconds=0.01,
    )

    await asyncio.sleep(0)
    assert _FakeAgentClient.sent_requests == []

    await _wait_for_sent_request_count(1)
    assert len(_FakeAgentClient.sent_requests) == 1


@pytest.mark.asyncio
async def test_reconnect_cancels_scheduled_disconnect_cancel() -> None:
    handler = _TestMessageHandler.create()
    _seed_stream_task(
        handler, rid="rid-reconnect", channel_id="tui", session_id="sess_reconnect",
    )

    await handler.schedule_cancel_agent_sessions_on_disconnect(
        [],
        stale_request_keys=[("tui", "rid-reconnect")],
        delay_seconds=0.03,
    )

    assert handler.cancel_scheduled_disconnect_cancel("tui", "sess_reconnect") is True
    await asyncio.sleep(0.05)

    assert _FakeAgentClient.sent_requests == []


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


# ---------- ChannelMode.is_team_mode ----------


@pytest.mark.asyncio
async def test_team_mq_publish_does_not_register_publisher_as_godview() -> None:
    handler = _TestMessageHandler.create()

    await handler._maybe_register_godview(
        _team_transport_message(
            request_id="publisher-request",
            method=ReqMethod.TEAM_MQ_PUBLISH,
            ws_id="publisher-ws",
        )
    )

    registry = handler.get_session_sharing_registry()
    assert registry.lookup_member("sess-godview", SubRole.GODVIEW) == []


@pytest.mark.asyncio
async def test_godview_registration_is_unique_per_websocket() -> None:
    handler = _TestMessageHandler.create()

    first = _team_transport_message(
        request_id="web-request-1",
        method=ReqMethod.CHAT_SEND,
        ws_id="web-ws-1",
    )
    second = _team_transport_message(
        request_id="web-request-2",
        method=ReqMethod.CHAT_SEND,
        ws_id="web-ws-2",
    )
    await handler._maybe_register_godview(first)
    await handler._maybe_register_godview(first)
    await handler._maybe_register_godview(second)

    registry = handler.get_session_sharing_registry()
    subscriptions = registry.lookup_member("sess-godview", SubRole.GODVIEW)
    assert {sub.delivery.ws_id for sub in subscriptions} == {"web-ws-1", "web-ws-2"}
    assert len(subscriptions) == 2


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("team", True),
        ("code.team", True),
        ("team.plan", True),
        ("agent.plan", False),
        ("agent.fast", False),
        ("code.plan", False),
        ("code.normal", False),
        ("", False),
        ("  team  ", True),   # strip
        ("Team", True),       # case-insensitive
    ],
)
def test_is_team_mode(mode: str, expected: bool) -> None:
    assert ChannelMode.is_team_mode(mode) is expected


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("team", True),
        ("code.team", True),
        ("team.plan", True),
        ("agent.plan", False),
    ],
)
def test_is_team_chat_send_recognizes_all_team_modes(mode: str, expected: bool) -> None:
    _is_team_chat_send = getattr(MessageHandler, "_is_team_chat_send")
    msg = _chat_send_message(channel_id="web", session_id="sess", mode=mode)
    assert _is_team_chat_send(msg) is expected


# ---------------------------------------------------------------- chat.steer


def _chat_steer_message(
    *,
    channel_id: str = "web",
    session_id: str = "sess_steer",
    mode: str = "agent",
) -> Message:
    """The canonical steering request."""
    return Message(
        id="req-steer",
        type="req",
        channel_id=channel_id,
        session_id=session_id,
        params={"mode": mode, "query": "prefer the async client"},
        timestamp=0.0,
        ok=True,
        req_method=ReqMethod.CHAT_STEER,
        is_stream=False,
    )


def _legacy_steer_message(*, field: str = "input_mode", mode: str = "agent") -> Message:
    """The legacy form: chat.send carrying input_mode/runtime_mode = steer."""
    return Message(
        id="req-legacy-steer",
        type="req",
        channel_id="web",
        session_id="sess_steer",
        params={"mode": mode, "query": "prefer the async client", field: "steer"},
        timestamp=0.0,
        ok=True,
        req_method=ReqMethod.CHAT_SEND,
        is_stream=True,
    )


def test_both_steer_wire_forms_are_recognised() -> None:
    """chat.steer and the legacy input_mode/runtime_mode form must converge."""
    assert MessageHandler._is_steer_message(_chat_steer_message())
    assert MessageHandler._is_steer_message(_legacy_steer_message(field="input_mode"))
    assert MessageHandler._is_steer_message(_legacy_steer_message(field="runtime_mode"))

    # Ordinary chat is not steering, and neither is a follow-up.
    assert not MessageHandler._is_steer_message(_chat_send_message())
    assert not MessageHandler._is_steer_message(
        _legacy_steer_message(field="input_mode").__class__(
            id="req-follow",
            type="req",
            channel_id="web",
            session_id="sess_steer",
            params={"mode": "agent", "query": "x", "input_mode": "follow_up"},
            timestamp=0.0,
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            is_stream=True,
        )
    )


def test_steering_never_cancels_the_active_stream() -> None:
    """Acceptance: a steer is additive input, never a stream replacement."""
    should_cancel = MessageHandler._should_cancel_existing_stream_before_chat_send

    assert not should_cancel(_chat_steer_message())
    assert not should_cancel(_legacy_steer_message(field="input_mode"))
    assert not should_cancel(_legacy_steer_message(field="runtime_mode"))

    # Control: the guard must not have become a blanket "never cancel". An
    # ordinary chat.send still replaces the stream, which is the legacy flow.
    assert should_cancel(_chat_send_message())


def test_steer_is_chat_ordered_and_never_runs_in_background() -> None:
    """Steering must queue with chat, not race it.

    ``_non_stream_rpc_may_run_parallel`` lets short RPCs run concurrently so a
    slow one cannot block the forward loop. Chat methods are excluded because
    they have to reach the agent in the order they were enqueued. A steer that
    ran in background could arrive after a later steer, or overtake the
    interrupt meant to stop the very round it is steering.
    """
    from types import SimpleNamespace

    may_parallel = MessageHandler._non_stream_rpc_may_run_parallel

    assert not may_parallel(SimpleNamespace(method=ReqMethod.CHAT_STEER.value))
    # The chat methods that were already excluded stay excluded.
    for method in (ReqMethod.CHAT_SEND, ReqMethod.CHAT_CANCEL, ReqMethod.CHAT_ANSWER):
        assert not may_parallel(SimpleNamespace(method=method.value))
    # Control: a genuinely unrelated RPC still runs in parallel, so the
    # exclusion did not silently become "serialise everything".
    assert may_parallel(SimpleNamespace(method=ReqMethod.SESSION_LIST.value))


def test_steer_is_forwarded_by_both_channels_without_a_local_handler() -> None:
    """A method absent from both sets is dropped *and* answered METHOD_NOT_FOUND.

    The inbound callback returns False when the method is not in the forward
    set, so the request never reaches the AgentServer; the channel then finds no
    local handler and replies with an error. Steering is answered entirely
    agent-side — the ACK is the RPC reply — so it belongs in both sets, exactly
    like ``command.goal``.
    """
    from jiuwenswarm.gateway.channel_manager.web.app_web_handlers import (
        _FORWARD_NO_LOCAL_HANDLER_METHODS as WEB_NO_LOCAL,
        _FORWARD_REQ_METHODS as WEB_FORWARD,
    )
    from jiuwenswarm.gateway.channel_manager.tui.tui_connect import (
        CLI_FORWARD_NO_LOCAL_HANDLER_METHODS as TUI_NO_LOCAL,
        CLI_FORWARD_REQ_METHODS as TUI_FORWARD,
    )

    for forward, no_local in ((WEB_FORWARD, WEB_NO_LOCAL), (TUI_FORWARD, TUI_NO_LOCAL)):
        assert "chat.steer" in forward
        assert "chat.steer" in no_local
        # command.goal is the shape being copied: forwarded, no local handler.
        assert "command.goal" in forward and "command.goal" in no_local
        # chat.send is the contrast: forwarded, but it does have a local handler.
        assert "chat.send" in forward and "chat.send" not in no_local


def test_the_steer_ack_reaches_the_client_as_an_rpc_reply() -> None:
    """The hop every other steer test stops one short of.

    Both clients ``await`` the ACK as an RPC response correlated by frame id.
    ``_response_to_message`` converts any unary payload whose ``event_type``
    parses as an ``EventType`` into a ``type="event"`` frame -- and an event
    frame carries no reply for the awaited id, so the client times out while the
    steer has in fact already been queued.

    That is exactly what shipped: the ACK declared
    ``event_type: "chat.steer_ack"``, ``EventType`` had a matching member, and
    every unit test on both sides passed because none of them crossed this
    conversion.

    Two independent facts now prevent it -- the payload carries no
    ``event_type``, and ``EventType`` has no ``CHAT_STEER_ACK`` member -- and
    either alone is sufficient. That redundancy is why this test injects a
    member for the duration: without it, re-adding the payload key would not
    turn this test red, and the assertion would be guarding the enum deletion
    while its name promises otherwise. With it, the test fails if *the ACK
    builders* regress, which is the change someone would plausibly make.
    """
    from jiuwenswarm.common.schema.agent import AgentResponse
    from jiuwenswarm.gateway.message_handler.message_handler import MessageHandler
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )
    from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm
    from jiuwenswarm.common.schema.agent import AgentRequest
    from jiuwenswarm.common.schema.message import EventType

    request = AgentRequest(
        request_id="req-steer-1",
        channel_id="web",
        session_id="sess-1",
        req_method=ReqMethod.CHAT_STEER,
        params={"query": "prefer the async client"},
    )
    acks = (
        JiuWenSwarmDeepAdapter._steer_ack(
            request, accepted=True, reason=None, disposition="steer_queued"
        ),
        JiuWenSwarm._team_steer_ack(request, accepted=True, reason=None),
    )

    # Make "chat.steer_ack" parseable for the duration, so the conversion this
    # test guards against is actually reachable. Restored in the finally.
    original = dict(EventType._value2member_map_)
    sentinel = EventType.CHAT_STEER_APPLIED  # any member; only the mapping matters
    EventType._value2member_map_["chat.steer_ack"] = sentinel
    try:
        assert EventType("chat.steer_ack") is sentinel, "the injection must actually work"
        for ack in acks:
            msg = MessageHandler._response_to_message(ack, session_id="sess-1")
            assert msg.type == "res", (
                f"ACK became a {msg.type} frame; payload declares an event_type: {ack.payload}"
            )
            # The fields the clients read must survive the conversion.
            assert msg.payload["accepted"] is True
            assert msg.payload["request_id"] == "req-steer-1"
    finally:
        EventType._value2member_map_.clear()
        EventType._value2member_map_.update(original)

    # And the injection really is gone, so no later test inherits it.
    with pytest.raises(ValueError):
        EventType("chat.steer_ack")


def test_the_applied_event_still_becomes_an_event_frame() -> None:
    """Control for the test above.

    Without it, "no event_type anywhere in steering" would look like a valid
    simplification -- but chat.steer_applied is a genuine event riding the
    steered turn's stream, and stripping its name would leave clients with no
    subscription to match.
    """
    from jiuwenswarm.common.schema.agent import AgentResponse
    from jiuwenswarm.gateway.message_handler.message_handler import MessageHandler
    from jiuwenswarm.server.utils.stream_utils import steer_applied_payload

    applied = AgentResponse(
        request_id="req-steer-1",
        channel_id="web",
        ok=True,
        payload=steer_applied_payload({"applied": [{"id": "s1", "text": "x"}], "dropped": []}),
        metadata=None,
    )
    msg = MessageHandler._response_to_message(applied, session_id="sess-1")

    assert msg.type == "event"
    assert msg.event_type is not None
    assert msg.event_type.value == "chat.steer_applied"


def test_the_applied_event_keeps_its_structured_payload_on_web() -> None:
    """The applied / dropped id lists must not be flattened before the client."""
    from jiuwenswarm.gateway.channel_manager.web.web_connect import WebChannel

    assert WebChannel._should_preserve_full_payload("chat.steer_applied")


def test_chat_steer_is_not_promoted_to_a_stream() -> None:
    """Review point 5: the ACK is a one-shot reply, not an ACK-only short stream."""
    from jiuwenswarm.gateway.app_gateway import _normalize_gateway_message

    normalized = _normalize_gateway_message(_chat_steer_message())
    assert normalized.is_stream is False

    # chat.send is still promoted, so this asserts a difference rather than a
    # property that happens to hold for every method.
    assert _normalize_gateway_message(_chat_send_message()).is_stream is True
