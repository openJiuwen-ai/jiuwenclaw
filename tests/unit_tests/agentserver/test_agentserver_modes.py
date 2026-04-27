import asyncio
import json

import pytest

from jiuwenswarm.server import agent_ws_server as agent_ws_server_module
from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk


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
        ("team.plan", ("code", "team", "team.plan")),
        (None, ("agent", "plan", "agent.plan")),
    ],
)
def test_resolve_agent_request_mode_accepts_primary_and_dotted_modes(raw_mode, expected):
    assert agent_ws_server_module.resolve_agent_request_mode(raw_mode) == expected


def test_team_plan_params_are_team_mode():
    from jiuwenswarm.server.utils.utils import is_team_params

    assert is_team_params({"mode": "team.plan"})


def test_team_config_loader_ignores_yaml_enable_team_plan():
    from jiuwenswarm.agents.harness.team.config_loader import load_team_spec_dict

    spec = load_team_spec_dict(
        {
            "preferred_language": "zh",
            "models": {"defaults": [{"model_client_config": {}, "model_config_obj": {}}]},
            "modes": {
                "team": {
                    "demo": {
                        "team_name": "demo_team",
                        "enable_team_plan": "true",
                        "teammate_mode": "plan_mode",
                        "agents": {"leader": {}, "teammate": {}},
                    }
                }
            },
        }
    )

    assert "enable_team_plan" not in spec
    assert spec["teammate_mode"] == "plan_mode"


def test_team_plan_mode_sets_spec_field_without_metadata_package():
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
    from jiuwenswarm.agents.harness.team.team_manager import TeamManager

    spec = TeamAgentSpec.model_construct(
        team_name="demo_team",
        agents={},
        enable_team_plan=False,
        teammate_mode="build_mode",
        metadata={"keep": "value"},
    )

    TeamManager.apply_team_plan_mode(spec, request_metadata={"mode": "team.plan"})

    assert spec.enable_team_plan is True
    assert spec.teammate_mode == "build_mode"
    assert spec.metadata == {"keep": "value"}
    assert "team_plan" not in spec.metadata


def test_team_mode_does_not_enable_team_plan():
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
    from jiuwenswarm.agents.harness.team.team_manager import TeamManager

    spec = TeamAgentSpec.model_construct(
        team_name="demo_team",
        agents={},
        enable_team_plan=False,
    )

    TeamManager.apply_team_plan_mode(spec, request_metadata={"mode": "team"})

    assert spec.enable_team_plan is False


def test_code_team_mode_does_not_enable_team_plan():
    from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
    from jiuwenswarm.agents.harness.team.team_manager import TeamManager

    spec = TeamAgentSpec.model_construct(
        team_name="demo_team",
        agents={},
        enable_team_plan=False,
    )

    TeamManager.apply_team_plan_mode(spec, request_metadata={"mode": "code.team"})

    assert spec.enable_team_plan is False


def test_team_config_loader_defaults_teammate_mode_to_build_mode():
    from jiuwenswarm.agents.harness.team.config_loader import load_team_spec_dict

    spec = load_team_spec_dict(
        {
            "preferred_language": "zh",
            "models": {"defaults": [{"model_client_config": {}, "model_config_obj": {}}]},
            "modes": {
                "team": {
                    "demo": {
                        "team_name": "demo_team",
                        "agents": {"leader": {}, "teammate": {}},
                    }
                }
            },
        }
    )

    assert "enable_team_plan" not in spec
    assert spec["teammate_mode"] == "build_mode"


def test_resolve_request_project_dir_uses_metadata_project_dir_for_control_requests():
    request = AgentRequest(
        request_id="req-control",
        channel_id="tui",
        params={"cwd": "/tmp/current", "trusted_dirs": ["/tmp/trusted"]},
        metadata={"project_dir": "/tmp/project"},
    )

    assert agent_ws_server_module.resolve_request_project_dir(request) == "/tmp/project"


def test_resolve_request_project_dir_prefers_params_project_dir():
    request = AgentRequest(
        request_id="req-chat",
        channel_id="tui",
        params={
            "project_dir": "/tmp/project",
            "cwd": "/tmp/params",
            "trusted_dirs": ["/tmp/trusted"],
        },
        metadata={"project_dir": "/tmp/metadata-project", "cwd": "/tmp/metadata"},
    )

    assert agent_ws_server_module.resolve_request_project_dir(request) == "/tmp/project"


def test_resolve_request_project_dir_falls_back_to_cwd_for_legacy_clients():
    request = AgentRequest(
        request_id="req-chat",
        channel_id="tui",
        params={"cwd": "/tmp/params", "trusted_dirs": ["/tmp/trusted"]},
        metadata={"cwd": "/tmp/metadata"},
    )

    assert agent_ws_server_module.resolve_request_project_dir(request) == "/tmp/params"


def test_build_inputs_keeps_stable_project_dir_and_dynamic_cwd(monkeypatch):
    from jiuwenswarm.server.runtime.agent_adapter import interface as interface_module

    class FakeSkillManager:
        def __init__(self, workspace_dir=None):
            self.workspace_dir = workspace_dir
            self.hook = None

        def set_skillnet_install_complete_hook(self, hook):
            self.hook = hook

    class FakeSessionManager:
        @staticmethod
        def get_session_id(session_id):
            return session_id or "default"

        async def submit_and_wait(self, _session_id, task_func):
            return await task_func()

    class FakeAdapter:
        def __init__(self):
            self.seen_inputs = None
            self.skill_manager = None

        def set_skill_manager(self, skill_manager):
            self.skill_manager = skill_manager

        async def handle_heartbeat(self, _request):
            return None

        async def process_message_impl(self, request, inputs):
            self.seen_inputs = inputs
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={"content": "ok"},
            )

    fake_adapter = FakeAdapter()

    monkeypatch.setattr(
        interface_module,
        "get_config",
        lambda: {"preferred_language": "zh"},
    )
    monkeypatch.setattr(interface_module, "get_memory_mode", lambda _config: "disabled")
    monkeypatch.setattr(interface_module, "SkillManager", FakeSkillManager)
    monkeypatch.setattr(interface_module, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(interface_module, "append_history_record", lambda **_kwargs: None)
    monkeypatch.setattr(interface_module, "resolve_sdk_choice", lambda: "harness")
    monkeypatch.setattr(interface_module, "create_adapter", lambda _sdk, mode="agent": fake_adapter)
    request = AgentRequest(
        request_id="req-chat",
        channel_id="tui",
        session_id="tui_session",
        params={
            "query": "hello",
            "project_dir": "/tmp/project",
            "cwd": "/tmp/project-worktree",
            "trusted_dirs": ["/tmp/project"],
        },
    )

    asyncio.run(interface_module.JiuWenClaw().process_message(request))

    inputs = fake_adapter.seen_inputs
    assert inputs["project_dir"] == "/tmp/project"
    assert inputs["cwd"] == "/tmp/project-worktree"
    assert inputs["trusted_dirs"] == ["/tmp/project"]


def test_build_inputs_does_not_map_team_plan_approval_answers_to_interactive_input(monkeypatch):
    from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
    from jiuwenswarm.server.runtime.agent_adapter import interface as interface_module

    monkeypatch.setattr(interface_module, "get_config", lambda: {"preferred_language": "zh"})
    monkeypatch.setattr(interface_module, "get_memory_mode", lambda _config: "disabled")

    answers = [{"selected_options": ["Approve"], "custom_input": ""}]
    request = AgentRequest(
        request_id="req-answer",
        channel_id="tui",
        session_id="tui_session",
        params={
            "query": "",
            "request_id": "team_plan_approval_plan_rev1",
            "answers": answers,
            "source": "team_plan_approval",
        },
    )

    inputs, _, _ = interface_module.JiuWenClaw().build_inputs(request)

    assert not isinstance(inputs["query"], InteractiveInput)


def test_deep_adapter_handle_user_answer_ignores_team_plan_approval_compat(monkeypatch):
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenClawDeepAdapter

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.get_team_manager",
        lambda _channel_id: pytest.fail("team_plan_approval should not route via interact"),
    )

    adapter = JiuWenClawDeepAdapter()
    request = AgentRequest(
        request_id="req-answer",
        channel_id="tui",
        session_id="team-session",
        params={
            "request_id": "team_plan_approval_plan_rev1",
            "answers": [{"selected_options": ["Approve"], "custom_input": ""}],
            "source": "team_plan_approval",
        },
    )

    response = asyncio.run(adapter.handle_user_answer(request))

    assert response.payload["resolved"] is False


def test_build_inputs_threads_workspace_dir_into_cwd(monkeypatch, tmp_path):
    """``params.workspace_dir`` scopes a single prompt's cwd AND workspace to
    the supplied directory and creates it on demand. Threaded into BOTH
    ``inputs["cwd"]`` (so tools that read ``get_cwd()`` resolve relative paths
    against it) and ``inputs["workspace_dir"]`` (so the deep adapter forwards
    it as the workspace override on ``init_cwd``, which controls
    ``fs_operation``'s sandbox enforcement for absolute-path writes). Used by
    external drivers (IDE plugins, headless evaluators) that allocate a
    per-invocation scratch dir.
    """
    from jiuwenswarm.server.runtime.agent_adapter import interface as interface_module

    class FakeSkillManager:
        def __init__(self, workspace_dir=None):
            self.workspace_dir = workspace_dir
            self.hook = None

        def set_skillnet_install_complete_hook(self, hook):
            self.hook = hook

    class FakeSessionManager:
        @staticmethod
        def get_session_id(session_id):
            return session_id or "default"

        async def submit_and_wait(self, _session_id, task_func):
            return await task_func()

    class FakeAdapter:
        def __init__(self):
            self.seen_inputs = None
            self.skill_manager = None

        def set_skill_manager(self, skill_manager):
            self.skill_manager = skill_manager

        async def handle_heartbeat(self, _request):
            return None

        async def process_message_impl(self, request, inputs):
            self.seen_inputs = inputs
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={"content": "ok"},
            )

    fake_adapter = FakeAdapter()

    monkeypatch.setattr(interface_module, "get_config", lambda: {"preferred_language": "zh"})
    monkeypatch.setattr(interface_module, "get_memory_mode", lambda _config: "disabled")
    monkeypatch.setattr(interface_module, "SkillManager", FakeSkillManager)
    monkeypatch.setattr(interface_module, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(interface_module, "append_history_record", lambda **_kwargs: None)
    monkeypatch.setattr(interface_module, "resolve_sdk_choice", lambda: "harness")
    monkeypatch.setattr(interface_module, "create_adapter", lambda _sdk, mode="agent": fake_adapter)

    scratch = tmp_path / "scoped-run-001"  # does NOT exist yet
    assert not scratch.exists()

    request = AgentRequest(
        request_id="req-ws",
        channel_id="acp",
        session_id="acp_session",
        params={"query": "hello", "workspace_dir": str(scratch)},
    )

    asyncio.run(interface_module.JiuWenClaw().process_message(request))

    inputs = fake_adapter.seen_inputs
    # Path is resolved (symlinks followed, absolute form) before threading.
    resolved = str(scratch.resolve())
    assert inputs["cwd"] == resolved, "workspace_dir must thread into inputs.cwd"
    assert inputs["workspace_dir"] == resolved, (
        "workspace_dir must also thread into inputs.workspace_dir so the deep "
        "adapter forwards it as the workspace override on init_cwd"
    )
    assert scratch.is_dir(), "_build_inputs must mkdir the scratch dir"


def test_build_inputs_omits_cwd_when_workspace_dir_unset(monkeypatch):
    """When ``params.workspace_dir`` is absent or empty, ``_build_inputs``
    does not overwrite ``inputs.cwd`` -- letting the explicit ``params.cwd``
    (or the downstream default) win.
    """
    from jiuwenswarm.server.runtime.agent_adapter import interface as interface_module

    class FakeSkillManager:
        def __init__(self, workspace_dir=None):
            self.workspace_dir = workspace_dir
            self.hook = None

        def set_skillnet_install_complete_hook(self, hook):
            self.hook = hook

    class FakeSessionManager:
        @staticmethod
        def get_session_id(session_id):
            return session_id or "default"

        async def submit_and_wait(self, _session_id, task_func):
            return await task_func()

    class FakeAdapter:
        def __init__(self):
            self.seen_inputs = None
            self.skill_manager = None

        def set_skill_manager(self, skill_manager):
            self.skill_manager = skill_manager

        async def handle_heartbeat(self, _request):
            return None

        async def process_message_impl(self, request, inputs):
            self.seen_inputs = inputs
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={"content": "ok"},
            )

    fake_adapter = FakeAdapter()

    monkeypatch.setattr(interface_module, "get_config", lambda: {"preferred_language": "zh"})
    monkeypatch.setattr(interface_module, "get_memory_mode", lambda _config: "disabled")
    monkeypatch.setattr(interface_module, "SkillManager", FakeSkillManager)
    monkeypatch.setattr(interface_module, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(interface_module, "append_history_record", lambda **_kwargs: None)
    monkeypatch.setattr(interface_module, "resolve_sdk_choice", lambda: "harness")
    monkeypatch.setattr(interface_module, "create_adapter", lambda _sdk, mode="agent": fake_adapter)

    request = AgentRequest(
        request_id="req-nows",
        channel_id="acp",
        session_id="acp_session",
        params={"query": "hello", "cwd": "/tmp/explicit-cwd"},  # no workspace_dir
    )

    asyncio.run(interface_module.JiuWenClaw().process_message(request))

    inputs = fake_adapter.seen_inputs
    # params.cwd is preserved untouched
    assert inputs["cwd"] == "/tmp/explicit-cwd"


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


def test_agent_manager_creates_code_adapter_for_team_plan(monkeypatch):
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
        mode, sub_mode, canonical_mode = agent_ws_server_module.resolve_agent_request_mode("team.plan")
        await manager.get_agent(channel_id="tui", mode=mode, sub_mode=sub_mode)
        return canonical_mode

    canonical_mode = asyncio.run(run_case())

    assert canonical_mode == "team.plan"
    assert {"adapter_mode": "code"} in calls
    assert {
        "create_instance_mode": "code",
        "sub_mode": "team",
        "config": {},
    } in calls


def test_agent_manager_uses_project_dir_in_cache_identity(monkeypatch, tmp_path):
    from jiuwenswarm.server.runtime import agent_manager as agent_manager_module
    from jiuwenswarm.server.runtime.agent_adapter import interface as interface_module

    created = []

    class FakeSkillManager:
        def __init__(self, workspace_dir=None):
            self.workspace_dir = workspace_dir
            self.hook = None

        def set_skillnet_install_complete_hook(self, hook):
            self.hook = hook

    class FakeSessionManager:
        pass

    class FakeAdapter:
        def __init__(self):
            self.config = {}
            self.mode = "agent"
            self.sub_mode = None

        async def create_instance(self, config=None, *, mode="agent", sub_mode=None):
            self.config = config or {}
            self.mode = mode
            self.sub_mode = sub_mode
            created.append(self)

    def fake_create_adapter(sdk=None, *, mode="agent"):
        return FakeAdapter()

    monkeypatch.setattr(interface_module, "SkillManager", FakeSkillManager)
    monkeypatch.setattr(interface_module, "SessionManager", FakeSessionManager)
    monkeypatch.setattr(interface_module, "get_agent_workspace_dir", lambda: "workspace")
    monkeypatch.setattr(interface_module, "resolve_sdk_choice", lambda: "harness")
    monkeypatch.setattr(interface_module, "create_adapter", fake_create_adapter)

    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()

    async def run_case():
        manager = agent_manager_module.AgentManager()
        first = await manager.get_agent(channel_id="tui", mode="agent", project_dir=str(project_a))
        second = await manager.get_agent(channel_id="tui", mode="agent", project_dir=str(project_b))
        first_again = await manager.get_agent(channel_id="tui", mode="agent", project_dir=str(project_a))
        return first, second, first_again

    first, second, first_again = asyncio.run(run_case())

    assert first is first_again
    assert first is not second
    assert len(created) == 2
