from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import jiuwenswarm.common.config as config_module
from jiuwenswarm.agents.harness.common.tools.heartbeat_runtime import (
    HEARTBEAT_TOOL_NAMES,
    HeartbeatRuntimeBridge,
)
from jiuwenswarm.common.schema.agent import AgentResponseChunk
from jiuwenswarm.common.schema.message import EventType, Message
from jiuwenswarm.gateway.app_gateway import _resolve_health_check_config
from jiuwenswarm.gateway.health_check.health_check import (
    HEALTH_CHECK_OK,
    HEALTH_CHECK_PROMPT,
    GatewayHealthCheckService,
    HealthCheckConfig,
)
from jiuwenswarm.gateway.channel_manager.im_platforms.dingtalk.dingtalk_connect import (
    DingTalkChannel,
)
from jiuwenswarm.gateway.channel_manager.im_platforms.telegram.telegram_connect import (
    TelegramChannel,
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
    assert "HEARTBEAT.md" not in HEALTH_CHECK_PROMPT
    assert "不要执行用户任务" in HEALTH_CHECK_PROMPT


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


@pytest.mark.parametrize("payload_key", ["health_check", "heartbeat"])
def test_telegram_and_dingtalk_read_health_check_relay_payload(
    payload_key: str,
) -> None:
    msg = Message(
        id="health-check-relay",
        type="event",
        channel_id="telegram",
        session_id="health_check_1",
        params={},
        timestamp=1.0,
        ok=True,
        payload={payload_key: "probe result"},
        event_type=EventType.HEALTH_CHECK_RELAY,
    )
    assert TelegramChannel._extract_content(None, msg) == "probe result"
    assert DingTalkChannel._extract_message_content(None, msg) == "probe result"


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
    assert "finite max_runs" in create_tool.card.description
    assert "do not add run-count bookkeeping" in create_tool.card.description
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


@pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")
async def test_agent_heartbeat_runtime_restores_omitted_optional_defaults() -> None:
    push = _Push()
    bridge = HeartbeatRuntimeBridge(gateway_push=push)
    context = SimpleNamespace(
        channel_id="web",
        session_id="session-1",
        metadata={"request_id": "request-1"},
    )
    tools = bridge.build_tools(context=context)
    create_tool = next(
        item for item in tools if item.card.name == "heartbeat_create_job"
    )

    await create_tool.invoke(
        {
            "name": "finite follow-up",
            "prompt": "say three animal words",
            "schedule": {"type": "interval", "interval_seconds": 60},
        }
    )

    sent_data = push.messages[0]["body"]["data"]
    assert sent_data["name"] == "finite follow-up"
    assert sent_data["prompt"] == "say three animal words"
    assert sent_data["schedule"]["interval_seconds"] == 60
    assert sent_data.get("enabled", True) is True
    assert sent_data.get("delete_after_run", False) is False
    assert all(value is not None for value in sent_data.values())

    tool_by_name = {item.card.name: item for item in tools}
    await tool_by_name["heartbeat_list_jobs"].invoke({})
    await tool_by_name["heartbeat_preview_job"].invoke({"job_id": "hb-1"})
    await tool_by_name["heartbeat_run_now"].invoke({"job_id": "hb-1"})
    await tool_by_name["heartbeat_cancel_run"].invoke({"job_id": "hb-1"})
    assert [message["body"]["data"] for message in push.messages[1:]] == [
        {"scope": "current"},
        {"job_id": "hb-1", "count": 5},
        {"job_id": "hb-1", "reschedule": False},
        {"job_id": "hb-1", "pause_schedule": False},
    ]
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


async def test_agent_heartbeat_runtime_rejects_legacy_one_way_transport() -> None:
    class LegacyPush:
        async def send_push(self, msg: dict) -> None:
            raise AssertionError("one-way fallback must not be used")

    context = SimpleNamespace(
        channel_id="web",
        session_id="session-1",
        metadata={"request_id": "request-1"},
    )
    with pytest.raises(RuntimeError, match="authoritative responses"):
        await HeartbeatRuntimeBridge(gateway_push=LegacyPush())._send(
            context, "list", {"scope": "current"}
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


async def test_agent_server_gateway_push_timeout_cleans_waiter() -> None:
    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    server._current_ws = object()
    server._current_send_lock = asyncio.Lock()
    server._gateway_push_waiters = {}

    async def send_push(msg: dict) -> None:
        return None

    server.send_push = send_push  # type: ignore[method-assign]
    with pytest.raises(TimeoutError, match="heartbeat operation timed out"):
        await server.request_gateway_push(
            {"body": {"action": "list", "data": {}}}, timeout_seconds=0.001
        )
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


async def test_message_handler_defers_exact_heartbeat_claim_after_busy_race() -> None:
    class Scheduler:
        def __init__(self) -> None:
            self.calls = []

        async def on_session_busy_after_dispatch(
            self, job_id: str, run_id: str
        ) -> bool:
            self.calls.append((job_id, run_id))
            return True

    class Handler:
        def __init__(self) -> None:
            self._heartbeat_scheduler_service = Scheduler()

    handler = Handler()
    deferred = await MessageHandler._defer_heartbeat_run_for_busy_session(
        handler,
        "request-fallback",
        {
            "automation": {
                "kind": "heartbeat",
                "job_id": "job-1",
                "run_id": "run-1",
            }
        },
    )

    assert deferred is True
    assert handler._heartbeat_scheduler_service.calls == [("job-1", "run-1")]


@pytest.mark.parametrize(
    "event_type",
    [
        EventType.CHAT_DELTA,
        EventType.CHAT_FINAL,
        EventType.CHAT_PROCESSING_STATUS,
    ],
)
def test_message_handler_preserves_automation_metadata_on_stream_events(
    event_type: EventType,
) -> None:
    metadata = {
        "automation": {
            "kind": "heartbeat",
            "job_id": "job-1",
            "run_id": "run-1",
        }
    }
    message = MessageHandler._chunk_to_message(
        AgentResponseChunk(
            request_id="run-1",
            channel_id="web",
            payload={"event_type": event_type.value, "content": "event"},
        ),
        "bound-session",
        metadata=metadata,
    )
    assert message.event_type == event_type
    assert message.metadata == metadata


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
            self._stream_sessions = {"run-1": "s1", "manual-run": "s1"}
            self._stream_channels = {"run-1": "web", "manual-run": "web"}
            self._stream_modes = {"run-1": "agent", "manual-run": "agent"}
            self._stream_metadata = {
                "run-1": {"automation": {"kind": "heartbeat"}},
                "manual-run": None,
            }
            self.popped = []
            self.remote_cancelled = []

        async def _pop_stream_tracking_and_broadcast(self, request_ids) -> None:  # noqa: ANN001
            self.popped.extend(request_ids)

        async def _cancel_agent_work_for_session(self, msg, session_id, **kwargs):  # noqa: ANN001
            self.remote_cancelled.append((msg, session_id, kwargs))
            exact.cancel()
            await asyncio.gather(exact, return_exceptions=True)
            await self._pop_stream_tracking_and_broadcast(["run-1"])
            return True

    handler = Handler()
    try:
        await asyncio.sleep(0)
        assert await MessageHandler.cancel_request(handler, "run-1") is True
        assert cancelled == ["exact"]
        assert sibling.done() is False
        assert handler.popped == ["run-1"]
        assert handler.remote_cancelled[0][1] == "s1"
        assert handler.remote_cancelled[0][2]["target_request_id"] == "run-1"
        assert handler.remote_cancelled[0][2]["cancel_gateway_tasks"] is False
    finally:
        sibling.cancel()
        await asyncio.gather(sibling, return_exceptions=True)


def test_heartbeat_chat_send_never_replaces_manual_stream() -> None:
    msg = Message(
        id="heartbeat-run",
        type="req",
        channel_id="web",
        session_id="s1",
        params={"query": "follow up"},
        timestamp=1.0,
        ok=True,
        req_method=__import__(
            "jiuwenswarm.common.schema.message", fromlist=["ReqMethod"]
        ).ReqMethod.CHAT_SEND,
        metadata={"automation": {"kind": "heartbeat"}},
    )
    assert MessageHandler._should_cancel_existing_stream_before_chat_send(msg) is False


async def test_agent_server_exact_cancel_selects_only_target_request() -> None:
    async def wait_forever() -> None:
        await asyncio.Event().wait()

    first = asyncio.create_task(wait_forever())
    sibling = asyncio.create_task(wait_forever())
    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    server._session_stream_tasks = {
        "s1": {first: asyncio.Event(), sibling: asyncio.Event()}
    }
    server._stream_request_ids = {first: "heartbeat-run", sibling: "manual-run"}
    try:
        selected = server._cancel_stream_entries(
            "s1", target_request_id="heartbeat-run"
        )
        assert [task for task, _event in selected] == [first]
        assert server._cancel_stream_entries(
            "s1", target_request_id="missing"
        ) == []
    finally:
        first.cancel()
        sibling.cancel()
        await asyncio.gather(first, sibling, return_exceptions=True)


async def test_exact_remote_cancel_failure_keeps_gateway_stream_alive() -> None:
    async def wait_forever() -> None:
        await asyncio.Event().wait()

    stream = asyncio.create_task(wait_forever())

    class Handler:
        _stream_tasks = {"heartbeat-run": stream}
        _stream_sessions = {"heartbeat-run": "s1"}
        _stream_channels = {"heartbeat-run": "web"}
        _stream_modes = {"heartbeat-run": "agent"}
        _channel_states = {}

        @staticmethod
        def _clear_session_evolution_states(_session_id) -> None:  # noqa: ANN001
            return None

        @staticmethod
        async def _prepare_agent_dispatch_message(msg):  # noqa: ANN001
            return msg

        @staticmethod
        def message_to_e2a(msg):  # noqa: ANN001
            return SimpleNamespace(channel_context={}, request_id=msg.id)

        @staticmethod
        async def _send_non_stream_agent_request(_env):  # noqa: ANN001
            return SimpleNamespace(
                ok=False,
                request_id="cancel-request",
                payload={
                    "event_type": "chat.interrupt_result",
                    "success": False,
                },
            )

        @staticmethod
        async def _pop_stream_tracking_and_broadcast(_request_ids) -> None:  # noqa: ANN001
            raise AssertionError("failed remote cancel must preserve local stream")

    cancel_msg = Message(
        id="cancel-request",
        type="req",
        channel_id="web",
        session_id="s1",
        params={"mode": "agent"},
        timestamp=1.0,
        ok=True,
    )
    try:
        cancelled = await MessageHandler._cancel_agent_work_for_session(
            Handler(),
            cancel_msg,
            "s1",
            publish_interrupt_result=False,
            channel_id="web",
            cancel_gateway_tasks=False,
            target_request_id="heartbeat-run",
            cancel_local_on_agent_failure=False,
        )
        assert cancelled is False
        assert stream.done() is False
    finally:
        stream.cancel()
        await asyncio.gather(stream, return_exceptions=True)


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
