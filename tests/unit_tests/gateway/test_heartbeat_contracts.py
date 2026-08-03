from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.tools.heartbeat_runtime import (
    HEARTBEAT_TOOL_NAMES,
    HeartbeatRuntimeBridge,
)
from jiuwenswarm.common.schema.message import EventType
from jiuwenswarm.gateway.app_gateway import _resolve_health_check_config
from jiuwenswarm.gateway.health_check.health_check import (
    HEALTH_CHECK_OK,
    GatewayHealthCheckService,
    HealthCheckConfig,
)
from jiuwenswarm.gateway.message_handler.message_handler import MessageHandler


class _Push:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_push(self, msg: dict) -> None:
        self.messages.append(msg)


class _AgentClient:
    def __init__(self) -> None:
        self.envelopes: list = []

    async def send_request(self, envelope):  # noqa: ANN001
        self.envelopes.append(envelope)
        return SimpleNamespace(payload={"heartbeat": HEALTH_CHECK_OK})


class _Relay:
    def __init__(self) -> None:
        self.messages: list = []

    async def publish_robot_messages(self, msg) -> None:  # noqa: ANN001
        self.messages.append(msg)


async def test_health_check_keeps_legacy_internal_protocol_and_new_relay() -> None:
    client = _AgentClient()
    relay = _Relay()
    service = GatewayHealthCheckService(
        client,
        HealthCheckConfig(interval_seconds=60, relay_channel_id="web"),
        message_handler=relay,
    )
    await service._tick()
    envelope = client.envelopes[0]
    assert envelope.session_id.startswith("heartbeat_")
    assert envelope.channel == "__heartbeat__"
    assert "HEARTBEAT_OK" in envelope.params["heartbeat"]
    assert relay.messages[0].event_type == EventType.HEALTH_CHECK_RELAY


def test_health_check_config_prefers_new_section_and_reads_legacy() -> None:
    assert _resolve_health_check_config(
        {"health_check": {"every": 10}, "heartbeat": {"every": 20}}
    ) == {"every": 10}
    assert _resolve_health_check_config(
        {"heartbeat": {"every": 20, "target": "web", "jobs": {"x": 1}}}
    ) == {"every": 20, "target": "web"}


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
async def test_agent_heartbeat_runtime_builds_nine_tools_and_forwards_context() -> None:
    push = _Push()
    bridge = HeartbeatRuntimeBridge(gateway_push=push)
    context = SimpleNamespace(
        channel_id="tui",
        session_id="session-1",
        metadata={"request_id": "request-1"},
    )
    tools = bridge.build_tools(context=context)
    assert {item.card.name for item in tools} == HEARTBEAT_TOOL_NAMES
    create_tool = next(item for item in tools if item.card.name == "heartbeat_create_job")
    assert "cron_create_job" in create_tool.card.description
    assert "heartbeat_update_job(enabled=false)" in create_tool.card.description
    assert "heartbeat_cancel_run(pause_schedule=true)" in create_tool.card.description
    await bridge._send(context, "list", {"scope": "current"})
    sent = push.messages[0]
    assert sent["channel_id"] == "tui"
    assert sent["session_id"] == "session-1"
    assert sent["response_kind"] == "heartbeat"
    del tools
    __import__("gc").collect()


async def test_gateway_heartbeat_push_uses_session_scoped_controller() -> None:
    calls: list[tuple] = []

    class Controller:
        async def list_jobs(self, params, *, access_session_id=None):  # noqa: ANN001
            calls.append((params, access_session_id))
            return {"jobs": []}

    class Handler:
        _heartbeat_controller = Controller()
        _heartbeat_scheduler_service = None

        def __init__(self) -> None:
            self.messages = []

        async def publish_robot_messages(self, msg) -> None:  # noqa: ANN001
            self.messages.append(msg)

    handler = Handler()
    await MessageHandler._handle_heartbeat_push_payload(
        handler,
        payload={"action": "list", "data": {"scope": "current"}},
        request_id="r1",
        channel_id="web",
        session_id="s1",
        metadata=None,
    )
    assert calls == [({"scope": "current"}, "s1")]
    assert handler.messages[0].payload["result"] == {"jobs": []}


async def test_gateway_accepts_team_session_deleted_push() -> None:
    class Scheduler:
        def __init__(self) -> None:
            self.calls = []

        async def on_session_deleted(self, session_id: str) -> None:
            self.calls.append(session_id)

    class Handler:
        _heartbeat_controller = object()

        def __init__(self) -> None:
            self._heartbeat_scheduler_service = Scheduler()
            self.messages = []

        async def publish_robot_messages(self, msg) -> None:  # noqa: ANN001
            self.messages.append(msg)

    handler = Handler()
    await MessageHandler._handle_heartbeat_push_payload(
        handler,
        payload={
            "action": "session_deleted",
            "data": {"session_id": "team-session"},
        },
        request_id="delete-team",
        channel_id="__heartbeat__",
        session_id="team-session",
        metadata=None,
    )
    assert handler._heartbeat_scheduler_service.calls == ["team-session"]
    assert handler.messages == []


async def test_message_handler_reports_exact_heartbeat_completion() -> None:
    class Scheduler:
        def __init__(self) -> None:
            self.calls = []

        async def on_run_finished(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            self.calls.append((args, kwargs))

    class Handler:
        def __init__(self) -> None:
            self._heartbeat_scheduler_service = Scheduler()

    handler = Handler()
    await MessageHandler._notify_heartbeat_run_finished(
        handler,
        "request-fallback",
        {"automation": {"kind": "heartbeat", "job_id": "job-1", "run_id": "run-1"}},
        outcome="failed",
        error="boom",
    )
    assert handler._heartbeat_scheduler_service.calls == [
        (("job-1", "run-1"), {"outcome": "failed", "error": "boom"})
    ]


async def test_message_handler_cancel_request_only_stops_exact_stream() -> None:
    cancelled: list[str] = []

    async def wait_forever(name: str) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(name)
            raise

    exact = asyncio.create_task(wait_forever("exact"))
    sibling = asyncio.create_task(wait_forever("sibling"))

    class Handler:
        def __init__(self) -> None:
            self._stream_tasks = {"run-1": exact, "manual-run": sibling}
            self.popped = []

        async def _pop_stream_tracking_and_broadcast(self, request_ids) -> None:  # noqa: ANN001
            self.popped.extend(request_ids)

    handler = Handler()
    try:
        await asyncio.sleep(0)
        assert await MessageHandler.cancel_request(handler, "run-1") is True
        assert cancelled == ["exact"]
        assert sibling.done() is False
        assert handler.popped == ["run-1"]
    finally:
        sibling.cancel()
        await asyncio.gather(sibling, return_exceptions=True)
