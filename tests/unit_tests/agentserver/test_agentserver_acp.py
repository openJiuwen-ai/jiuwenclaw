import asyncio
import json
import types

import pytest
from openjiuwen.agent_teams.runtime import RunActionKind

from jiuwenclaw.server import agent_ws_server as agent_ws_server_module
from jiuwenclaw.server.runtime.agent_manager import ACP_DEFAULT_CAPABILITIES
from jiuwenclaw.agents.harness.common.tools import acp_output_tools
from jiuwenclaw.agents.harness.common.tools.acp_output_tools import AcpOutputRequest, get_acp_output_manager
from jiuwenclaw.server.runtime.agent_adapter import interface_deep as interface_deep_module
from jiuwenclaw.server.runtime.agent_adapter import team_helpers as team_helpers_module
from jiuwenclaw.server.utils.stream_utils import parse_stream_chunk
from jiuwenclaw.server.runtime.agent_adapter.interface_deep import (
    _build_context_assemble_rail,
    _build_context_processor_rail,
)
from jiuwenclaw.common.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenclaw.common.schema.agent import AgentRequest
from jiuwenclaw.common.schema.message import ReqMethod


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


class FakeAgentManager:
    def __init__(self, *, capabilities=None, session_id="sess-created", client_capabilities=None):
        self.capabilities = capabilities
        self.session_id = session_id
        self.client_capabilities = client_capabilities or {}
        self.initialize_calls = []
        self.create_session_calls = []

    async def initialize(self, channel_id="", extra_config=None):
        self.initialize_calls.append(
            {"channel_id": channel_id, "extra_config": extra_config}
        )
        return self.capabilities

    async def create_session(self, channel_id="", session_id=None):
        self.create_session_calls.append({"channel_id": channel_id, "session_id": session_id})
        return session_id or self.session_id

    def get_client_capabilities(self, channel_id=""):
        return dict(self.client_capabilities)


class FakeTeamManager:
    def __init__(self):
        self.prepare_session_switch_calls = []
        self.cleared_active_sessions = []
        self.cleared_pending_sessions = []
        self.popped_stream_tasks = []
        self.active_session_id = None
        self.active_team_name = None
        self.pending_session_id = None
        self.pending_team_name = None

    async def prepare_session_switch(self, session_id: str, reason: str = "") -> None:
        self.prepare_session_switch_calls.append(
            {"session_id": session_id, "reason": reason}
        )

    def pop_stream_task(self, session_id: str):
        self.popped_stream_tasks.append(session_id)
        return None

    def clear_active_runtime(self, session_id: str) -> None:
        self.cleared_active_sessions.append(session_id)

    def clear_pending_runtime(self, session_id: str) -> None:
        self.cleared_pending_sessions.append(session_id)


class FakeContextProcessorRail:
    def __init__(self, *, processors=None, preset=None):
        self.processors = processors
        self.preset = preset


class FakeContextAssembleRail:
    def __init__(self):
        pass


class AgentWebSocketServerHarness(agent_ws_server_module.AgentWebSocketServer):
    def __init__(self):
        super().__init__()
        self._find_team_session_ids_override = None

    def set_agent_manager_for_test(self, agent_manager):
        self._agent_manager = agent_manager

    def set_find_team_session_ids_override_for_test(self, override):
        self._find_team_session_ids_override = override

    async def handle_initialize_for_test(self, ws, request, send_lock):
        await self._handle_initialize(ws, request, send_lock)

    async def handle_session_create_for_test(self, ws, request, send_lock):
        await self._handle_session_create(ws, request, send_lock)

    async def handle_session_switch_for_test(self, ws, request, send_lock):
        await self._handle_session_switch(ws, request, send_lock)

    async def handle_team_delete_for_test(self, ws, request, send_lock):
        await self._handle_team_delete(ws, request, send_lock)

    async def handle_message_for_test(self, ws, raw, send_lock):
        await self._handle_message(ws, raw, send_lock)

    async def find_team_session_ids_for_test(self, team_name):
        return await self._find_team_session_ids(team_name)

    async def _find_team_session_ids(self, team_name: str):
        if self._find_team_session_ids_override is not None:
            return await self._find_team_session_ids_override(team_name)
        return await super()._find_team_session_ids(team_name)


class DeepAdapterHarness(interface_deep_module.JiuWenClawDeepAdapter):
    def build_context_assemble_rail_for_test(self):
        return _build_context_assemble_rail()

    def build_context_processor_rail_for_test(self, config):
        return _build_context_processor_rail(config)


class TeamHelpersHarness:
    @staticmethod
    def sync_team_identity_metadata_for_test(**kwargs) -> None:
        getattr(team_helpers_module, "_sync_team_identity_metadata")(**kwargs)


def fake_encode_agent_response_for_wire(resp, response_id):
    return {
        "response_id": response_id,
        "payload": resp.payload,
        "ok": resp.ok,
    }


@pytest.fixture(autouse=True)
def _reset_acp_output_manager():
    mgr = get_acp_output_manager()
    mgr.reset_state()
    mgr.set_send_push_callback(None)
    yield
    mgr.reset_state()
    mgr.set_send_push_callback(None)


def test_interface_deep_parse_stream_chunk_preserves_tool_update():
    parsed = parse_stream_chunk(
        types.SimpleNamespace(
            type="tool_update",
            payload={
                "tool_update": {
                    "tool_call_id": "call-1",
                    "tool_name": "read_file",
                    "status": "in_progress",
                }
            },
        )
    )

    assert parsed == {
        "event_type": "chat.tool_update",
        "tool_call_id": "call-1",
        "tool_name": "read_file",
        "status": "in_progress",
    }


def test_parse_stream_chunk_serializes_team_runtime_enum_kind():
    parsed = parse_stream_chunk(
        {
            "type": "team.runtime_ready",
            "activation_kind": RunActionKind.NEW_TEAM_IN_SESSION,
            "team_name": "demo-team",
        }
    )

    assert parsed == {
        "event_type": "team.runtime_ready",
        "activation_kind": RunActionKind.NEW_TEAM_IN_SESSION.value,
        "team_name": "demo-team",
    }


def test_sync_team_identity_metadata_updates_only_for_create_kinds(monkeypatch):
    updates = []

    monkeypatch.setattr(
        team_helpers_module,
        "get_session_metadata",
        lambda _session_id: {"mode": "team"},
    )
    monkeypatch.setattr(
        team_helpers_module,
        "update_session_metadata",
        lambda **kwargs: updates.append(kwargs),
    )

    TeamHelpersHarness.sync_team_identity_metadata_for_test(
        channel_id="web",
        session_id="team_sess_001",
        mode="team",
        ready_team_name="demo-team",
        activation_kind=RunActionKind.CREATE.value,
    )

    assert updates == [
        {
            "session_id": "team_sess_001",
            "channel_id": "web",
            "mode": "team",
            "team_name": "demo-team",
        }
    ]


def test_sync_team_identity_metadata_skips_recover_kinds(monkeypatch):
    updates = []

    monkeypatch.setattr(
        team_helpers_module,
        "get_session_metadata",
        lambda _session_id: {"mode": "team", "team_name": "existing-team"},
    )
    monkeypatch.setattr(
        team_helpers_module,
        "update_session_metadata",
        lambda **kwargs: updates.append(kwargs),
    )

    TeamHelpersHarness.sync_team_identity_metadata_for_test(
        channel_id="web",
        session_id="team_sess_001",
        mode="team",
        ready_team_name="new-team",
        activation_kind=RunActionKind.NEW_TEAM_IN_SESSION.value,
    )

    assert updates == []


def test_sync_team_identity_metadata_keeps_existing_name_on_mismatch(monkeypatch):
    updates = []

    monkeypatch.setattr(
        team_helpers_module,
        "get_session_metadata",
        lambda _session_id: {"mode": "team", "team_name": "existing-team"},
    )
    monkeypatch.setattr(
        team_helpers_module,
        "update_session_metadata",
        lambda **kwargs: updates.append(kwargs),
    )

    TeamHelpersHarness.sync_team_identity_metadata_for_test(
        channel_id="web",
        session_id="team_sess_001",
        mode="team",
        ready_team_name="new-team",
        activation_kind=RunActionKind.CREATE.value,
    )

    assert updates == []


@pytest.mark.asyncio
async def test_handle_initialize_uses_agent_manager_capabilities(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_manager = FakeAgentManager(capabilities={"protocolVersion": "9.9.9"})
    server.set_agent_manager_for_test(fake_manager)
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    request = AgentRequest(
        request_id="req-init",
        channel_id="acp",
        req_method=ReqMethod.INITIALIZE,
        params={
            "protocolVersion": "0.1.0",
            "clientCapabilities": {"fs": {"readTextFile": True}},
        },
    )

    await server.handle_initialize_for_test(fake_ws, request, asyncio.Lock())

    assert fake_manager.initialize_calls == [
        {
            "channel_id": "acp",
            "extra_config": {
                "protocol_version": "0.1.0",
                "client_capabilities": {"fs": {"readTextFile": True}},
            },
        }
    ]
    assert fake_ws.sent == [
        {
            "response_id": "req-init",
            "payload": {"protocolVersion": "9.9.9"},
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_initialize_falls_back_to_default_capabilities(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_manager = FakeAgentManager(capabilities=None)
    server.set_agent_manager_for_test(fake_manager)
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    request = AgentRequest(
        request_id="req-init-default",
        channel_id="acp",
        req_method=ReqMethod.INITIALIZE,
        params={},
    )

    await server.handle_initialize_for_test(fake_ws, request, asyncio.Lock())

    assert fake_ws.sent == [
        {
            "response_id": "req-init-default",
            "payload": ACP_DEFAULT_CAPABILITIES,
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_session_create_returns_session_id(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_manager = FakeAgentManager(session_id="acp_session_001")
    server.set_agent_manager_for_test(fake_manager)
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    request = AgentRequest(
        request_id="req-session-create",
        channel_id="acp",
        req_method=ReqMethod.SESSION_CREATE,
        params={},
    )

    await server.handle_session_create_for_test(fake_ws, request, asyncio.Lock())

    assert fake_manager.create_session_calls == [{"channel_id": "acp", "session_id": None}]
    assert fake_ws.sent == [
        {
            "response_id": "req-session-create",
            "payload": {"sessionId": "acp_session_001"},
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_session_create_returns_explicit_session_id(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_manager = FakeAgentManager(session_id="unused-default")
    server.set_agent_manager_for_test(fake_manager)
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    request = AgentRequest(
        request_id="req-session-create-explicit",
        channel_id="acp",
        req_method=ReqMethod.SESSION_CREATE,
        params={"session_id": "sess_explicit_001"},
    )

    await server.handle_session_create_for_test(fake_ws, request, asyncio.Lock())

    assert fake_manager.create_session_calls == [
        {"channel_id": "acp", "session_id": "sess_explicit_001"}
    ]
    assert fake_ws.sent == [
        {
            "response_id": "req-session-create-explicit",
            "payload": {"sessionId": "sess_explicit_001"},
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_session_create_stops_old_team_runtime_for_team_mode(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_manager = FakeAgentManager(session_id="unused-default")
    fake_team_manager = FakeTeamManager()
    server.set_agent_manager_for_test(fake_manager)
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.get_team_manager",
        lambda channel_id: fake_team_manager,
    )

    request = AgentRequest(
        request_id="req-session-create-team",
        channel_id="web",
        req_method=ReqMethod.SESSION_CREATE,
        params={"mode": "team", "session_id": "team_sess_001"},
    )

    await server.handle_session_create_for_test(fake_ws, request, asyncio.Lock())

    assert fake_manager.create_session_calls == [
        {"channel_id": "web", "session_id": "team_sess_001"}
    ]
    assert fake_team_manager.prepare_session_switch_calls == [
        {"session_id": "team_sess_001", "reason": "session.create switch: "}
    ]
    assert fake_ws.sent == [
        {
            "response_id": "req-session-create-team",
            "payload": {"sessionId": "team_sess_001"},
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_session_switch_stops_old_team_runtime_for_team_mode(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_team_manager = FakeTeamManager()
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.get_team_manager",
        lambda channel_id: fake_team_manager,
    )

    request = AgentRequest(
        request_id="req-session-switch-team",
        channel_id="web",
        req_method=ReqMethod.SESSION_SWITCH,
        params={"mode": "team", "session_id": "team_sess_002"},
    )

    await server.handle_session_switch_for_test(fake_ws, request, asyncio.Lock())

    assert fake_team_manager.prepare_session_switch_calls == [
        {"session_id": "team_sess_002", "reason": "session.switch: "}
    ]
    assert fake_ws.sent == [
        {
            "response_id": "req-session-switch-team",
            "payload": {
                "session_id": "team_sess_002",
                "mode": "team",
                "switched": True,
            },
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_session_switch_rejects_non_team_mode(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_team_manager = FakeTeamManager()
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.get_team_manager",
        lambda channel_id: fake_team_manager,
    )

    request = AgentRequest(
        request_id="req-session-switch-agent",
        channel_id="web",
        req_method=ReqMethod.SESSION_SWITCH,
        params={"mode": "agent.plan", "session_id": "sess_agent_001"},
    )

    await server.handle_session_switch_for_test(fake_ws, request, asyncio.Lock())

    assert fake_team_manager.prepare_session_switch_calls == []
    assert fake_ws.sent == [
        {
            "response_id": "req-session-switch-agent",
            "payload": {
                "error": "session.switch is only supported for team mode",
                "code": "UNSUPPORTED_MODE",
            },
            "ok": False,
        }
    ]


@pytest.mark.asyncio
async def test_handle_team_delete_deletes_all_matching_team_sessions(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_team_manager_web = FakeTeamManager()
    fake_team_manager_cli = FakeTeamManager()
    fake_ws = FakeWebSocket()
    delete_calls = []
    removed_dirs = []
    stop_calls = []
    cleared_metadata_cache = []

    async def fake_delete_agent_team(*, team_name, session_ids, force):
        delete_calls.append(
            {"team_name": team_name, "session_ids": session_ids, "force": force}
        )
        return True

    async def fake_find_team_session_ids(team_name: str):
        assert team_name == "jiuwen_team"
        return ["team_sess_001", "team_sess_002"]

    class FakeSessionDir:
        def __init__(self, session_id: str):
            self.session_id = session_id
            self.path = session_id

        @staticmethod
        def exists() -> bool:
            return True

    class FakeSessionsRoot:
        def __init__(self) -> None:
            self._prefix = "sessions/"

        def __truediv__(self, session_id: str):
            session_dir = FakeSessionDir(session_id)
            session_dir.path = f"{self._prefix}{session_id}"
            return session_dir

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.get_all_team_managers",
        lambda: [fake_team_manager_web, fake_team_manager_cli],
    )
    server.set_find_team_session_ids_override_for_test(fake_find_team_session_ids)
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.stop_team_session_runtime_across_managers",
        lambda session_id, reason="": stop_calls.append(
            {"session_id": session_id, "reason": reason}
        ) or asyncio.sleep(0, result=True),
    )
    monkeypatch.setattr(
        "openjiuwen.core.runner.Runner.delete_agent_team",
        fake_delete_agent_team,
    )
    monkeypatch.setattr(
        agent_ws_server_module,
        "get_agent_sessions_dir",
        lambda: FakeSessionsRoot(),
    )
    monkeypatch.setattr(
        agent_ws_server_module.shutil,
        "rmtree",
        lambda path: removed_dirs.append(path.session_id),
    )
    monkeypatch.setattr(
        agent_ws_server_module,
        "remove_session_metadata_cache",
        lambda session_id: cleared_metadata_cache.append(session_id),
    )

    request = AgentRequest(
        request_id="req-team-delete",
        channel_id="web",
        req_method=ReqMethod.TEAM_DELETE,
        params={"mode": "team", "team_name": "jiuwen_team"},
    )

    await server.handle_team_delete_for_test(fake_ws, request, asyncio.Lock())

    assert delete_calls == [
        {
            "team_name": "jiuwen_team",
            "session_ids": ["team_sess_001", "team_sess_002"],
            "force": True,
        }
    ]
    assert stop_calls == [
        {"session_id": "team_sess_001", "reason": "team.delete: "},
        {"session_id": "team_sess_002", "reason": "team.delete: "},
    ]
    assert removed_dirs == ["team_sess_001", "team_sess_002"]
    assert cleared_metadata_cache == ["team_sess_001", "team_sess_002"]
    assert fake_team_manager_web.popped_stream_tasks == ["team_sess_001", "team_sess_002"]
    assert fake_team_manager_web.cleared_active_sessions == ["team_sess_001", "team_sess_002"]
    assert fake_team_manager_web.cleared_pending_sessions == ["team_sess_001", "team_sess_002"]
    assert fake_team_manager_cli.popped_stream_tasks == ["team_sess_001", "team_sess_002"]
    assert fake_team_manager_cli.cleared_active_sessions == ["team_sess_001", "team_sess_002"]
    assert fake_team_manager_cli.cleared_pending_sessions == ["team_sess_001", "team_sess_002"]
    assert fake_ws.sent == [
        {
            "response_id": "req-team-delete",
            "payload": {
                "team_name": "jiuwen_team",
                "session_ids": ["team_sess_001", "team_sess_002"],
                "deleted": True,
            },
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_team_delete_rejects_non_team_mode(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    request = AgentRequest(
        request_id="req-team-delete-agent",
        channel_id="web",
        req_method=ReqMethod.TEAM_DELETE,
        params={"mode": "agent.plan", "team_name": "jiuwen_team"},
    )

    await server.handle_team_delete_for_test(fake_ws, request, asyncio.Lock())

    assert fake_ws.sent == [
        {
            "response_id": "req-team-delete-agent",
            "payload": {
                "error": "team.delete is only supported for team mode",
                "code": "UNSUPPORTED_MODE",
            },
            "ok": False,
        }
    ]


@pytest.mark.asyncio
async def test_handle_team_delete_requires_team_name(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    request = AgentRequest(
        request_id="req-team-delete-missing-name",
        channel_id="web",
        req_method=ReqMethod.TEAM_DELETE,
        params={"mode": "team"},
    )

    await server.handle_team_delete_for_test(fake_ws, request, asyncio.Lock())

    assert fake_ws.sent == [
        {
            "response_id": "req-team-delete-missing-name",
            "payload": {
                "error": "team_name is required",
                "code": "BAD_REQUEST",
            },
            "ok": False,
        }
    ]


@pytest.mark.asyncio
async def test_find_team_session_ids_uses_metadata_team_name(monkeypatch, tmp_path):
    server = AgentWebSocketServerHarness()
    sessions_root = tmp_path / "sessions"
    (sessions_root / "team_sess_001").mkdir(parents=True)
    (sessions_root / "team_sess_002").mkdir(parents=True)
    (sessions_root / "agent_sess_003").mkdir(parents=True)

    metadata_map = {
        "team_sess_001": {"mode": "team", "team_name": "jiuwen_team"},
        "team_sess_002": {"mode": "team", "team_name": "other_team"},
        "agent_sess_003": {"mode": "agent.plan", "team_name": "jiuwen_team"},
    }

    monkeypatch.setattr(
        agent_ws_server_module,
        "get_agent_sessions_dir",
        lambda: sessions_root,
    )
    monkeypatch.setattr(
        "jiuwenclaw.server.runtime.session.session_metadata.get_session_metadata",
        lambda session_id: metadata_map.get(session_id, {}),
    )

    session_ids = await server.find_team_session_ids_for_test("jiuwen_team")

    assert session_ids == ["team_sess_001"]


@pytest.mark.asyncio
async def test_handle_acp_tool_response_completes_pending_future(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_ws = FakeWebSocket()
    mgr = get_acp_output_manager()
    future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
    mgr.add_pending_request(AcpOutputRequest(
        jsonrpc_id="42",
        method="fs/read_text_file",
        params={"path": "workspace/demo.txt"},
        future=future,
        request_id="req-pending",
    ))

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    request = AgentRequest(
        request_id="req-acp-tool-response",
        channel_id="acp",
        req_method=ReqMethod.ACP_TOOL_RESPONSE,
        params={
            "jsonrpc_id": "42",
            "response": {
                "jsonrpc": "2.0",
                "id": "42",
                "result": {"content": "hello"},
            },
        },
    )

    await server.handle_acp_tool_response_for_test(fake_ws, request, asyncio.Lock())

    assert future.done() is True
    assert future.result() == {
        "jsonrpc": "2.0",
        "id": "42",
        "result": {"content": "hello"},
    }
    assert fake_ws.sent == [
        {
            "response_id": "req-acp-tool-response",
            "payload": {"accepted": True},
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_acp_tool_response_unknown_id_is_soft_ignored(monkeypatch):
    server = AgentWebSocketServerHarness()
    fake_ws = FakeWebSocket()

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    request = AgentRequest(
        request_id="req-acp-tool-response-unknown",
        channel_id="acp",
        req_method=ReqMethod.ACP_TOOL_RESPONSE,
        params={
            "jsonrpc_id": "unknown-42",
            "response": {
                "jsonrpc": "2.0",
                "id": "unknown-42",
                "result": {"content": "late"},
            },
        },
    )

    await server.handle_acp_tool_response_for_test(fake_ws, request, asyncio.Lock())

    assert fake_ws.sent == [
        {
            "response_id": "req-acp-tool-response-unknown",
            "payload": {
                "accepted": False,
                "ignored": True,
                "reason": "unknown_or_late_response",
                "jsonrpc_id": "unknown-42",
            },
            "ok": True,
        }
    ]


@pytest.mark.asyncio
async def test_handle_message_uses_ws_scoped_acp_client_capabilities(monkeypatch):
    ws_a = FakeWebSocket()
    ws_b = FakeWebSocket()
    server = AgentWebSocketServerHarness()
    fake_manager = FakeAgentManager(
        capabilities=ACP_DEFAULT_CAPABILITIES,
        client_capabilities={"fs": {"readTextFile": True}},
    )
    server.set_agent_manager_for_test(fake_manager)

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )

    init_request_a = AgentRequest(
        request_id="req-init-a",
        channel_id="acp",
        req_method=ReqMethod.INITIALIZE,
        params={"clientCapabilities": {"fs": {"readTextFile": True}}},
    )
    init_request_b = AgentRequest(
        request_id="req-init-b",
        channel_id="acp",
        req_method=ReqMethod.INITIALIZE,
        params={"clientCapabilities": {"terminal": {"create": True}}},
    )
    await server.handle_initialize_for_test(ws_a, init_request_a, asyncio.Lock())
    await server.handle_initialize_for_test(ws_b, init_request_b, asyncio.Lock())

    captured = {}

    async def fake_handle_session_create(ws, request, send_lock):
        captured[id(ws)] = dict(request.metadata or {})

    monkeypatch.setattr(server, "_handle_session_create", fake_handle_session_create)

    env = e2a_from_agent_fields(
        request_id="req-session-create",
        channel_id="acp",
        session_id="sess-b",
        req_method=ReqMethod.SESSION_CREATE,
        params={"session_id": "sess-b"},
        is_stream=False,
        timestamp=0.0,
    )
    await server.handle_message_for_test(ws_b, json.dumps(env.to_dict(), ensure_ascii=False), asyncio.Lock())

    assert captured[id(ws_b)]["acp_client_capabilities"] == {"terminal": {"create": True}}


@pytest.mark.asyncio
async def test_wait_for_terminal_exit_returns_soft_timeout(monkeypatch):
    mgr = get_acp_output_manager()
    captured: dict[str, object] = {}

    async def _fake_send_jsonrpc_request(
        method,
        params,
        *,
        channel_id="acp",
        session_id=None,
        timeout=0.0,
    ):
        captured["method"] = method
        captured["params"] = params
        captured["channel_id"] = channel_id
        captured["session_id"] = session_id
        captured["timeout"] = timeout
        raise asyncio.TimeoutError

    monkeypatch.setattr(mgr, "send_jsonrpc_request", _fake_send_jsonrpc_request)
    monkeypatch.setattr(acp_output_tools, "_ACP_WAIT_FOR_EXIT_TIMEOUT_SECONDS", 123.0)

    result = await acp_output_tools.wait_for_terminal_exit("term-soft-timeout", session_id="sess-soft")

    assert captured == {
        "method": "terminal/wait_for_exit",
        "params": {"terminalId": "term-soft-timeout"},
        "channel_id": "acp",
        "session_id": "sess-soft",
        "timeout": 123.0,
    }
    assert result == {
        "exitCode": None,
        "signal": None,
        "timedOut": True,
        "running": True,
        "shouldRetry": True,
    }


@pytest.mark.asyncio
async def test_wait_for_terminal_exit_completed_result_sets_should_retry_false(monkeypatch):
    mgr = get_acp_output_manager()

    async def _fake_send_jsonrpc_request(
        method,
        params,
        *,
        channel_id="acp",
        session_id=None,
        timeout=0.0,
    ):
        return {
            "jsonrpc": "2.0",
            "id": "ok-1",
            "result": {"exitCode": 0, "signal": None},
        }

    monkeypatch.setattr(mgr, "send_jsonrpc_request", _fake_send_jsonrpc_request)

    result = await acp_output_tools.wait_for_terminal_exit("term-done", session_id="sess-done")

    assert result == {
        "exitCode": 0,
        "signal": None,
        "timedOut": False,
        "running": False,
        "shouldRetry": False,
    }


def test_build_context_processor_rail_uses_summary_offloader_config(monkeypatch):
    monkeypatch.setattr(
        interface_deep_module,
        "ContextProcessorRail",
        FakeContextProcessorRail,
    )
    adapter = DeepAdapterHarness()

    rail = adapter.build_context_processor_rail_for_test(
        {
            "context_engine_config": {
                "message_summary_offloader_config": {
                    "tokens_threshold": 5000,
                    "keep_last_round": False,
                },
                "dialogue_compressor_config": {"tokens_threshold": 100000},
            }
        }
    )

    assert isinstance(rail, FakeContextProcessorRail)
    assert rail.preset is True
    assert rail.processors == [
        (
            "MessageSummaryOffloader",
            {
                "tokens_threshold": 5000,
                "keep_last_round": False,
            },
        ),
        ("DialogueCompressor", {"tokens_threshold": 100000}),
    ]


def test_build_context_processor_rail_prefers_summary_offloader_config(monkeypatch):
    monkeypatch.setattr(
        interface_deep_module,
        "ContextProcessorRail",
        FakeContextProcessorRail,
    )
    adapter = DeepAdapterHarness()

    rail = adapter.build_context_processor_rail_for_test(
        {
            "context_engine_config": {
                "message_summary_offloader_config": {
                    "tokens_threshold": 6000,
                },
                "message_offloader_config": {
                    "tokens_threshold": 5000,
                },
            }
        }
    )

    assert isinstance(rail, FakeContextProcessorRail)
    assert rail.processors == [
        ("MessageSummaryOffloader", {"tokens_threshold": 6000}),
    ]


def test_build_context_assemble_rail_returns_context_assemble_rail_instance(monkeypatch):
    monkeypatch.setattr(
        interface_deep_module,
        "ContextAssembleRail",
        FakeContextAssembleRail,
    )
    adapter = DeepAdapterHarness()

    rail = adapter.build_context_assemble_rail_for_test()

    assert isinstance(rail, FakeContextAssembleRail)
