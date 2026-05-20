import asyncio
import json

import pytest

from jiuwenswarm.server import agent_ws_server as agent_ws_server_module
from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponseChunk


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


class AgentWebSocketServerHarness(agent_ws_server_module.AgentWebSocketServer):
    async def handle_stream_for_test(self, ws, request, send_lock):
        await self._handle_stream(ws, request, send_lock)


def fake_encode_agent_chunk_for_wire(chunk, response_id, sequence):
    return {
        "response_id": response_id,
        "sequence": sequence,
        "payload": chunk.payload,
        "is_complete": chunk.is_complete,
    }


@pytest.mark.parametrize(
    ("raw_mode", "expected"),
    [
        ("team", ("team", None, "team")),
        ("agent", ("agent", "plan", "agent.plan")),
        ("code", ("code", "normal", "code.normal")),
        ("agent.fast", ("agent", "fast", "agent.fast")),
        ("code.plan", ("code", "plan", "code.plan")),
        ("code.team", ("code", "team", "code.team")),
        (None, ("agent", "plan", "agent.plan")),
    ],
)
def test_resolve_agent_request_mode_accepts_primary_and_dotted_modes(raw_mode, expected):
    assert agent_ws_server_module.resolve_agent_request_mode(raw_mode) == expected


def test_handle_stream_accepts_team_mode_without_sub_mode(monkeypatch):
    class FakeAgent:
        def __init__(self):
            self.seen_request = None

        async def process_message_stream(self, request):
            self.seen_request = request
            yield AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={"event_type": "chat.done"},
                is_complete=True,
            )

    class FakeAgentManager:
        def __init__(self):
            self.agent = FakeAgent()
            self.calls = []

        async def get_agent(self, channel_id, mode, project_dir=None, sub_mode=None):
            self.calls.append(
                {
                    "channel_id": channel_id,
                    "mode": mode,
                    "project_dir": project_dir,
                    "sub_mode": sub_mode,
                }
            )
            return self.agent

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_chunk_for_wire",
        fake_encode_agent_chunk_for_wire,
    )

    async def run_case():
        server = AgentWebSocketServerHarness()
        fake_manager = FakeAgentManager()
        monkeypatch.setattr(server.get_agent_manager(), "get_agent", fake_manager.get_agent)
        fake_ws = FakeWebSocket()
        request = AgentRequest(
            request_id="req-team",
            channel_id="feishu",
            params={"mode": "team", "query": "hello"},
            is_stream=True,
        )

        await server.handle_stream_for_test(fake_ws, request, asyncio.Lock())
        return fake_manager, fake_ws, request

    fake_manager, fake_ws, request = asyncio.run(run_case())

    assert fake_manager.calls == [
        {
            "channel_id": "feishu",
            "mode": "team",
            "project_dir": None,
            "sub_mode": None,
        }
    ]
    assert fake_manager.agent.seen_request is request
    assert request.params["mode"] == "team"
    assert fake_ws.sent == [
        {
            "response_id": "req-team",
            "sequence": 0,
            "payload": {"event_type": "chat.done"},
            "is_complete": True,
        }
    ]


def test_handle_stream_accepts_code_team_sub_mode(monkeypatch):
    class FakeAgent:
        def __init__(self):
            self.seen_request = None

        async def process_message_stream(self, request):
            self.seen_request = request
            yield AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={"event_type": "chat.done"},
                is_complete=True,
            )

    class FakeAgentManager:
        def __init__(self):
            self.agent = FakeAgent()
            self.calls = []

        async def get_agent(self, channel_id, mode, project_dir=None, sub_mode=None):
            self.calls.append(
                {
                    "channel_id": channel_id,
                    "mode": mode,
                    "project_dir": project_dir,
                    "sub_mode": sub_mode,
                }
            )
            return self.agent

    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_chunk_for_wire",
        fake_encode_agent_chunk_for_wire,
    )

    async def run_case():
        server = AgentWebSocketServerHarness()
        fake_manager = FakeAgentManager()
        monkeypatch.setattr(server.get_agent_manager(), "get_agent", fake_manager.get_agent)
        fake_ws = FakeWebSocket()
        request = AgentRequest(
            request_id="req-code-team",
            channel_id="tui",
            params={"mode": "code.team", "query": "hello"},
            is_stream=True,
        )

        await server.handle_stream_for_test(fake_ws, request, asyncio.Lock())
        return fake_manager, fake_ws, request

    fake_manager, fake_ws, request = asyncio.run(run_case())

    assert fake_manager.calls == [
        {
            "channel_id": "tui",
            "mode": "code",
            "project_dir": None,
            "sub_mode": "team",
        }
    ]
    assert fake_manager.agent.seen_request is request
    assert request.params["mode"] == "code.team"
    assert fake_ws.sent == [
        {
            "response_id": "req-code-team",
            "sequence": 0,
            "payload": {"event_type": "chat.done"},
            "is_complete": True,
        }
    ]


def test_agent_manager_creates_code_adapter_for_code_team(monkeypatch):
    from jiuwenswarm.server.runtime import agent_manager as agent_manager_module
    from jiuwenswarm.server.runtime.agent_adapter import interface as interface_module

    calls = []

    class FakeSkillManager:
        def __init__(self, workspace_dir=None):
            self.workspace_dir = workspace_dir
            self.hook = None

        def set_skillnet_install_complete_hook(self, hook):
            self.hook = hook

    class FakeSessionManager:
        pass

    class FakeAdapter:
        async def create_instance(self, config=None, *, mode="agent", sub_mode=None):
            calls.append(
                {
                    "create_instance_mode": mode,
                    "sub_mode": sub_mode,
                    "config": config,
                }
            )

    def fake_create_adapter(sdk=None, *, mode="agent"):
        calls.append({"adapter_mode": mode})
        return FakeAdapter()

    monkeypatch.setattr(interface_module, "SkillManager", FakeSkillManager)
    monkeypatch.setattr(interface_module, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(interface_module, "get_agent_workspace_dir", lambda: "workspace")
    monkeypatch.setattr(interface_module, "resolve_sdk_choice", lambda: "harness")
    monkeypatch.setattr(interface_module, "create_adapter", fake_create_adapter)

    async def run_case():
        manager = agent_manager_module.AgentManager()
        await manager.get_agent(channel_id="tui", mode="code", sub_mode="team")

    asyncio.run(run_case())

    assert {"adapter_mode": "code"} in calls
    assert {
        "create_instance_mode": "code",
        "sub_mode": "team",
        "config": {},
    } in calls
