from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.tools.heartbeat_runtime import (
    HEARTBEAT_TOOL_NAMES,
    HeartbeatRuntimeBridge,
)
from jiuwenswarm.common.schema.message import EventType, Message
import jiuwenswarm.common.config as config_module
from jiuwenswarm.gateway.app_gateway import _resolve_health_check_config
from jiuwenswarm.gateway.health_check.health_check import (
    HEALTH_CHECK_OK,
    GatewayHealthCheckService,
    HealthCheckConfig,
)
from jiuwenswarm.gateway.message_handler.message_handler import MessageHandler
from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer


class _Push:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_push(self, msg: dict) -> None:
        self.messages.append(msg)

    async def request(self, msg: dict, *, timeout_seconds: float) -> dict:
        self.messages.append(msg)
        return {"ok": True, "data": {"jobs": [{"id": "hb-real"}]}}


class _AgentClient:
    def __init__(self) -> None:
        self.envelopes: list = []

    async def send_request(self, envelope):  # noqa: ANN001
        self.envelopes.append(envelope)
        return SimpleNamespace(payload={"health_check": HEALTH_CHECK_OK})


class _Relay:
    def __init__(self) -> None:
        self.messages: list = []

    async def publish_robot_messages(self, msg) -> None:  # noqa: ANN001
        self.messages.append(msg)


async def test_health_check_writes_new_internal_protocol_and_relay() -> None:
    client = _AgentClient()
    relay = _Relay()
    service = GatewayHealthCheckService(
        client,
        HealthCheckConfig(interval_seconds=60, relay_channel_id="web"),
        message_handler=relay,
    )
    await service._tick()
    envelope = client.envelopes[0]
    assert envelope.session_id.startswith("health_check_")
    assert envelope.channel == "__health_check__"
    assert "HEALTH_CHECK_OK" in envelope.params["health_check"]
    assert relay.messages[0].payload == {"health_check": HEALTH_CHECK_OK}
    assert relay.messages[0].event_type == EventType.HEALTH_CHECK_RELAY


async def test_health_check_reads_legacy_agent_response_during_upgrade() -> None:
    client = _AgentClient()
    client.send_request = lambda envelope: asyncio.sleep(  # type: ignore[method-assign]
        0, result=SimpleNamespace(payload={"heartbeat": "HEARTBEAT_OK"})
    )
    relay = _Relay()
    service = GatewayHealthCheckService(
        client,
        HealthCheckConfig(interval_seconds=60, relay_channel_id="web"),
        message_handler=relay,
    )
    await service._tick()
    assert relay.messages[0].payload == {"health_check": "HEARTBEAT_OK"}


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
    result = await bridge._send(context, "list", {"scope": "current"})
    assert result == {"jobs": [{"id": "hb-real"}]}
    sent = push.messages[0]
    assert sent["channel_id"] == "tui"
    assert sent["session_id"] == "session-1"
    assert sent["response_kind"] == "heartbeat"
    del tools
    __import__("gc").collect()


async def test_agent_heartbeat_runtime_surfaces_gateway_error() -> None:
    class FailedPush(_Push):
        async def request(self, msg: dict, *, timeout_seconds: float) -> dict:
            self.messages.append(msg)
            return {"ok": False, "code": "NOT_FOUND", "error": "job missing"}

    context = SimpleNamespace(
        channel_id="web",
        session_id="session-1",
        metadata={"request_id": "request-1"},
    )
    with pytest.raises(RuntimeError, match="NOT_FOUND: job missing"):
        await HeartbeatRuntimeBridge(gateway_push=FailedPush())._send(
            context, "get", {"job_id": "missing"}
        )


async def test_agent_server_correlates_gateway_push_result() -> None:
    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    server._current_ws = object()
    server._current_send_lock = asyncio.Lock()
    server._gateway_push_waiters = {}

    async def send_push(msg: dict) -> None:
        operation_id = msg["body"]["operation_id"]
        server._gateway_push_waiters[operation_id].set_result(
            {"ok": True, "data": {"id": "hb-1"}}
        )

    server.send_push = send_push  # type: ignore[method-assign]
    result = await server.request_gateway_push(
        {"body": {"action": "get", "data": {"job_id": "hb-1"}}}
    )
    assert result == {"ok": True, "data": {"id": "hb-1"}}
    assert server._gateway_push_waiters == {}


async def test_gateway_heartbeat_push_uses_session_scoped_controller() -> None:
    calls: list[tuple] = []

    class Controller:
        async def list_jobs(self, params, *, access_session_id=None):  # noqa: ANN001
            calls.append((params, access_session_id))
            return {"jobs": []}

    class Handler:
        _heartbeat_controller = Controller()
        _heartbeat_scheduler_service = None
        _reply_heartbeat_tool_operation = (
            MessageHandler._reply_heartbeat_tool_operation
        )

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


async def test_gateway_heartbeat_push_returns_correlated_authoritative_result() -> None:
    class Controller:
        async def get_job(self, job_id: str, *, access_session_id=None):  # noqa: ANN001
            return {"id": job_id, "session_id": access_session_id}

    class AgentClient:
        def __init__(self) -> None:
            self.envelopes = []

        async def send_request(self, envelope):  # noqa: ANN001
            self.envelopes.append(envelope)
            return SimpleNamespace(ok=True)

    class Handler:
        _heartbeat_controller = Controller()
        _heartbeat_scheduler_service = None
        _reply_heartbeat_tool_operation = (
            MessageHandler._reply_heartbeat_tool_operation
        )

        def __init__(self) -> None:
            self.agent_client = AgentClient()

    handler = Handler()
    await MessageHandler._handle_heartbeat_push_payload(
        handler,
        payload={
            "operation_id": "hbop-1",
            "action": "get",
            "data": {"job_id": "hb-1"},
        },
        request_id="r1",
        channel_id="web",
        session_id="s1",
        metadata=None,
    )
    envelope = handler.agent_client.envelopes[0]
    assert envelope.method == "heartbeat.tool_response"
    assert envelope.params["operation_id"] == "hbop-1"
    assert envelope.params["result"] == {
        "ok": True,
        "data": {"id": "hb-1", "session_id": "s1"},
        "error": None,
        "code": None,
    }


async def test_gateway_heartbeat_get_missing_returns_not_found() -> None:
    class Controller:
        async def get_job(self, job_id: str, *, access_session_id=None):  # noqa: ANN001
            return None

    class AgentClient:
        def __init__(self) -> None:
            self.envelopes = []

        async def send_request(self, envelope):  # noqa: ANN001
            self.envelopes.append(envelope)
            return SimpleNamespace(ok=True)

    class Handler:
        _heartbeat_controller = Controller()
        _heartbeat_scheduler_service = None
        _reply_heartbeat_tool_operation = (
            MessageHandler._reply_heartbeat_tool_operation
        )

        def __init__(self) -> None:
            self.agent_client = AgentClient()

    handler = Handler()
    await MessageHandler._handle_heartbeat_push_payload(
        handler,
        payload={"operation_id": "hbop-2", "action": "get", "data": {}},
        request_id="r2",
        channel_id="web",
        session_id="s1",
        metadata=None,
    )
    result = handler.agent_client.envelopes[0].params["result"]
    assert result["ok"] is False
    assert result["code"] == "NOT_FOUND"


def test_legacy_probe_config_migration_is_idempotent_and_preserves_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "heartbeat:\n  every: 30\n  target: web\n  jobs:\n    max_active_jobs_global: 7\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "CONFIG_YAML_PATH", config_path)
    assert config_module.migrate_legacy_heartbeat_probe_config() is True
    migrated = config_module.load_yaml_round_trip(config_path)
    assert migrated["health_check"] == {"every": 30, "target": "web"}
    assert migrated["heartbeat"] == {"jobs": {"max_active_jobs_global": 7}}
    assert config_module.migrate_legacy_heartbeat_probe_config() is False


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


def test_message_handler_heartbeat_preserves_bound_session_and_restores_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SessionMap:
        @staticmethod
        def get_session_id(*_identity) -> str:
            return "wrong-session-from-channel-map"

    class Handler:
        _control_channel_types = {"web"}
        _session_map_channel_types = {"web"}
        _session_map = SessionMap()

        @staticmethod
        def _resolve_control_channel_type(msg) -> str:  # noqa: ANN001
            return "web"

        @staticmethod
        def _extract_identity_tuple(msg):  # noqa: ANN001
            return ("web", "chat", "bot", "user")

        @staticmethod
        def _channel_id_matches_session_map_types(channel_id: str) -> bool:
            return True

        @staticmethod
        def get_or_create_channel_state(msg):  # noqa: ANN001
            return SimpleNamespace(
                session_id="wrong-session-from-channel-state",
                mode=SimpleNamespace(value="agent"),
            )

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda session_id, cache_bust=False, enable_writeback=True: {
            "mode": "code.team"
        },
    )
    msg = Message(
        id="heartbeat-run-1",
        type="req",
        channel_id="web",
        session_id="bound-session",
        params={"query": "continue"},
        timestamp=1.0,
        ok=True,
        metadata={"automation": {"kind": "heartbeat"}},
    )
    MessageHandler._apply_channel_state(Handler(), msg)
    assert msg.session_id == "bound-session"
    assert msg.params["mode"] == "code.team"
