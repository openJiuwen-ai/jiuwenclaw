# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team member skill state generation."""

import json
from pathlib import Path
from types import SimpleNamespace

from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec

from jiuwenswarm.agents.harness.team.team_manager import TeamManager


def test_member_skill_state_inherits_marketplaces_and_rebuilds_installed_skills(monkeypatch, tmp_path):
    """Member workspace state should keep marketplaces but only include copied skills."""
    global_skills_dir = tmp_path / "global_skills"
    global_skills_dir.mkdir(parents=True)
    for skill_name in ("skill-a", "skill-b"):
        skill_dir = global_skills_dir / skill_name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"---\ndescription: {skill_name}\n---\n", encoding="utf-8")

    (global_skills_dir / "skills_state.json").write_text(
        """
{
  "marketplaces": [{"name": "demo", "url": "https://example.com/demo.git", "enabled": true}],
  "installed_plugins": [
    {"name": "skill-a", "marketplace": "demo", "version": "1.0.0", "source": "demo"},
    {"name": "skill-b", "marketplace": "demo", "version": "1.0.0", "source": "demo"}
  ],
  "local_skills": [
    {"name": "skill-a", "origin": "/tmp/skill-a", "source": "demo"},
    {"name": "skill-b", "origin": "/tmp/skill-b", "source": "demo"}
  ]
}
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_skills_dir",
        lambda: global_skills_dir,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_runtime_inheritance.build_member_rails",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.plugins.rail_manager.get_rail_manager",
        lambda: type(
            "_DummyRailManager",
            (),
            {
                "get_registered_rail_names": lambda self: [],
                "load_rail_instance_without_enabled_check": lambda self, name: None,
            },
        )(),
    )

    spec = TeamAgentSpec.model_validate(
        {
            "team_name": "demo_team",
            "agents": {
                "leader": {},
                "member_a": {"skills": ["skill-a"]},
            },
        }
    )
    customizer = TeamManager.build_agent_customizer(
        spec=spec,
        deep_agent=type(
            "_DeepAgent",
            (),
            {
                "deep_config": type("_Config", (), {"sys_operation": None})(),
                "ability_manager": type("_AbilityManager", (), {"list": lambda self: []})(),
            },
        )(),
        session_id="session-1",
        request_id=None,
        channel_id=None,
        request_metadata=None,
    )

    member_root = tmp_path / "member_workspace"
    agent = type(
        "_Agent",
        (),
        {
            "deep_config": type(
                "_Config",
                (),
                {"workspace": type("_Workspace", (), {"root_path": str(member_root)})(), "sys_operation": None},
            )(),
            "ability_manager": type(
                "_AbilityManager",
                (),
                {
                    "list": lambda self: [],
                    "add": lambda self, card: None,
                },
            )(),
            "card": type("_Card", (), {"id": "member_a", "name": "member"})(),
            "add_rail": lambda self, rail: None,
        },
    )()

    customizer(agent, member_name="member_a", role="teammate")

    state_path = member_root / "skills" / "skills_state.json"
    assert state_path.is_file()

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["marketplaces"] == [
        {"name": "demo", "url": "https://example.com/demo.git", "enabled": True}
    ]
    assert [plugin["name"] for plugin in state["installed_plugins"]] == ["skill-a"]
    assert [skill["name"] for skill in state["local_skills"]] == ["skill-a"]
    assert Path(state["local_skills"][0]["origin"]).name == "skill-a"


def test_code_team_customizer_applies_code_profile_to_member(monkeypatch, tmp_path):
    """code.team parent agents should make each team member use the code profile."""
    global_skills_dir = tmp_path / "global_skills"
    global_skills_dir.mkdir(parents=True)
    (global_skills_dir / "skills_state.json").write_text(
        json.dumps({"marketplaces": [], "installed_plugins": [], "local_skills": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_skills_dir",
        lambda: global_skills_dir,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.build_member_rails",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.plugins.rail_manager.get_rail_manager",
        lambda: type(
            "_DummyRailManager",
            (),
            {
                "get_registered_rail_names": lambda self: [],
                "load_rail_instance_without_enabled_check": lambda self, name: None,
            },
        )(),
    )
    monkeypatch.setattr(
        TeamManager,
        "register_member_runtime_tools",
        staticmethod(lambda *args, **kwargs: None),
    )

    calls = []

    def fake_configure_code_member(agent, **kwargs):
        calls.append({"agent": agent, **kwargs})

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_code.configure_code_team_member_agent",
        fake_configure_code_member,
    )

    spec = TeamAgentSpec.model_validate(
        {
            "team_name": "demo_team",
            "agents": {
                "leader": {},
                "member_a": {},
            },
        }
    )
    parent_project = tmp_path / "project"
    parent_project.mkdir()
    parent_agent = SimpleNamespace(
        _jiuwenswarm_adapter_mode="code",
        _jiuwenswarm_code_project_dir=str(parent_project),
        deep_config=SimpleNamespace(
            workspace=SimpleNamespace(root_path=str(parent_project)),
            sys_operation=None,
        ),
        ability_manager=SimpleNamespace(list=lambda: []),
    )
    customizer = TeamManager.build_agent_customizer(
        spec=spec,
        deep_agent=parent_agent,
        session_id="session-1",
        request_id="request-1",
        channel_id="tui",
        request_metadata={"source": "test"},
    )

    class AbilityManager:
        def __init__(self):
            self.cards = []

        def list(self):
            return list(self.cards)

        def add(self, card):
            self.cards.append(card)

    class Agent:
        def __init__(self):
            self.deep_config = SimpleNamespace(
                workspace=SimpleNamespace(root_path=str(tmp_path / "member_workspace")),
                sys_operation=None,
            )
            self.ability_manager = AbilityManager()
            self.card = SimpleNamespace(id="member_a", name="member")
            self.rails = []

        def add_rail(self, rail):
            self.rails.append(rail)

    agent = Agent()

    customizer(agent, member_name="member_a", role="teammate")

    assert len(calls) == 1
    assert calls[0]["agent"] is agent
    assert calls[0]["parent_agent"] is parent_agent
    assert calls[0]["member_name"] == "member_a"
    assert calls[0]["role"] == "teammate"
    assert calls[0]["session_id"] == "session-1"
    assert calls[0]["channel_id"] == "tui"
    assert calls[0]["project_dir"] == str(parent_project)
    assert calls[0]["skill_manager"] is not None
    assert calls[0]["runtime_language"] is None
    assert calls[0]["force_english_runtime_prompt"] is True


def test_team_plan_leader_uses_preferred_language_for_code_profile(monkeypatch, tmp_path):
    """team.plan leader should not inherit code profile's English-only runtime prompt."""
    global_skills_dir = tmp_path / "global_skills"
    global_skills_dir.mkdir(parents=True)
    (global_skills_dir / "skills_state.json").write_text(
        json.dumps({"marketplaces": [], "installed_plugins": [], "local_skills": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_skills_dir",
        lambda: global_skills_dir,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.build_member_rails",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_config",
        lambda: {"preferred_language": "zh"},
    )
    monkeypatch.setattr(
        TeamManager,
        "register_member_runtime_tools",
        staticmethod(lambda *args, **kwargs: None),
    )

    calls = []

    def fake_configure_code_member(agent, **kwargs):
        calls.append({"agent": agent, **kwargs})

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_code.configure_code_team_member_agent",
        fake_configure_code_member,
    )

    spec = TeamAgentSpec.model_validate(
        {
            "team_name": "demo_team",
            "agents": {
                "leader": {},
                "member_a": {},
            },
            "language": "cn",
        }
    )
    parent_project = tmp_path / "project"
    parent_project.mkdir()
    parent_agent = SimpleNamespace(
        _jiuwenswarm_adapter_mode="code",
        _jiuwenswarm_code_project_dir=str(parent_project),
        deep_config=SimpleNamespace(
            workspace=SimpleNamespace(root_path=str(parent_project)),
            sys_operation=None,
        ),
        ability_manager=SimpleNamespace(list=lambda: []),
    )
    customizer = TeamManager.build_agent_customizer(
        spec=spec,
        deep_agent=parent_agent,
        session_id="session-1",
        request_id="request-1",
        channel_id="tui",
        request_metadata={"mode": "team.plan"},
    )

    class AbilityManager:
        def __init__(self):
            self.cards = []

        def list(self):
            return list(self.cards)

        def add(self, card):
            self.cards.append(card)

    class Agent:
        def __init__(self):
            self.deep_config = SimpleNamespace(
                workspace=SimpleNamespace(root_path=str(tmp_path / "leader_workspace")),
                sys_operation=None,
            )
            self.ability_manager = AbilityManager()
            self.card = SimpleNamespace(id="team_leader", name="leader")
            self.rails = []

        def add_rail(self, rail):
            self.rails.append(rail)

    agent = Agent()

    customizer(agent, member_name="team_leader", role="leader")

    assert len(calls) == 1
    assert calls[0]["agent"] is agent
    assert calls[0]["runtime_language"] == "cn"
    assert calls[0]["force_english_runtime_prompt"] is False


def test_configure_code_team_member_uses_relative_coding_memory_path(monkeypatch, tmp_path):
    """code.team members should register workspace directories with relative paths."""
    from jiuwenswarm.server.runtime.agent_adapter import interface_code

    global_workspace = tmp_path / "global_agent_workspace"
    member_workspace = tmp_path / "member_workspace"
    parent_project = tmp_path / "project"
    global_workspace.mkdir()
    member_workspace.mkdir()
    parent_project.mkdir()

    monkeypatch.setattr(interface_code, "get_config", lambda: {"react": {}})
    monkeypatch.setattr(interface_code, "get_agent_workspace_dir", lambda: global_workspace)
    monkeypatch.setattr(
        interface_code.JiuwenClawCodeAdapter,
        "_refresh_multimodal_configs",
        lambda self, config: None,
    )
    monkeypatch.setattr(
        interface_code.JiuwenClawCodeAdapter,
        "_create_model",
        lambda self, config: object(),
    )
    monkeypatch.setattr(
        interface_code.JiuwenClawCodeAdapter,
        "_create_sys_operation",
        lambda self: object(),
    )
    monkeypatch.setattr(
        interface_code.JiuwenClawCodeAdapter,
        "build_code_tool_cards",
        lambda self, agent_id: [],
    )
    monkeypatch.setattr(
        interface_code.JiuwenClawCodeAdapter,
        "_build_agent_rails",
        lambda self, react_config, config_base, mode: [],
    )
    monkeypatch.setattr(
        interface_code.JiuwenClawCodeAdapter,
        "_build_configured_subagents",
        lambda self, model, react_config, config_base: ([], False),
    )
    monkeypatch.setattr(
        interface_code.JiuwenClawCodeAdapter,
        "_extract_enabled_mcp_server_entries",
        lambda self, config_base: [],
    )

    class Workspace:
        def __init__(self, root_path):
            self.root_path = str(root_path)
            self.directories = []

        def set_directory(self, directory):
            self.directories.append(directory)

    class AbilityManager:
        @staticmethod
        def list():
            return []

        @staticmethod
        def add(card):
            raise AssertionError("no tool cards should be added in this test")

    workspace = Workspace(member_workspace)
    agent = SimpleNamespace(
        card=SimpleNamespace(id="counter-1", name="Counter 1"),
        deep_config=SimpleNamespace(
            workspace=workspace,
            model=None,
            sys_operation=None,
            subagents=[],
            mcps=[],
        ),
        ability_manager=AbilityManager(),
        add_rail=lambda rail: None,
    )
    parent_agent = SimpleNamespace(
        _jiuwenswarm_code_project_dir=str(parent_project),
        deep_config=SimpleNamespace(workspace=SimpleNamespace(root_path=str(parent_project))),
    )

    interface_code.configure_code_team_member_agent(
        agent,
        parent_agent=parent_agent,
        member_name="counter-1",
        role="counter",
    )

    coding_memory_path = Path(workspace.directories[0]["path"])
    assert not coding_memory_path.is_absolute()
    assert coding_memory_path.parts == ("coding_memory", parent_project.name)
