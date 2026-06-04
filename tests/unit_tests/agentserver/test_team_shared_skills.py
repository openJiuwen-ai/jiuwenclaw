# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team shared skills link logic."""

# pylint: disable=protected-access

import os
import shutil
from pathlib import Path

import pytest
from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec

from jiuwenswarm.agents.harness.team.rails.team_shared_skill_link_refresh_rail import (
    TeamSharedSkillLinkRefreshRail,
)
from jiuwenswarm.agents.harness.team.rails.team_member_skill_toolkit_rail import (
    MemberSkillToolkitRail,
)
from jiuwenswarm.agents.harness.team.team_manager import TeamManager
from jiuwenswarm.agents.harness.team.team_skill_links import link_skill_dir, remove_skill_dir_link


def _assert_link_points_to(path: Path, target: Path) -> None:
    """Assert that a link or junction resolves to the expected target."""
    assert path.exists()
    assert path.resolve() == target.resolve()


def test_ensure_team_shared_skills_initialized_links_global_skills(tmp_path, monkeypatch):
    """Global skills should be linked to team shared directory via the public helper."""
    # Create global skills directory
    global_skills_dir = tmp_path / "global_skills"
    global_skills_dir.mkdir(parents=True)
    for skill_name in ("skill-a", "skill-b"):
        skill_dir = global_skills_dir / skill_name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\n", encoding="utf-8")

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_skills_dir",
        lambda: global_skills_dir,
    )

    # Create team workspace config
    team_workspace = tmp_path / "team_workspace"
    team_workspace.mkdir(parents=True)
    team_shared_skills = team_workspace / "skills"

    # Build TeamAgentSpec with custom workspace path
    spec = TeamAgentSpec.model_validate(
        {
            "team_name": "demo_team",
            "agents": {
                "leader": {},
                "teammate": {},
            },
            "workspace": {"root_path": str(team_workspace), "enabled": True},
        }
    )

    manager = TeamManager()
    manager.ensure_team_shared_skills_initialized(spec)

    # The skills root stays a normal directory; individual skills are linked.
    assert team_shared_skills.is_dir()
    assert not team_shared_skills.is_symlink()
    _assert_link_points_to(team_shared_skills / "skill-a", global_skills_dir / "skill-a")
    _assert_link_points_to(team_shared_skills / "skill-b", global_skills_dir / "skill-b")
    assert not (team_shared_skills / "skills_state.json").exists()


def test_existing_skill_entry_is_not_replaced(tmp_path, monkeypatch):
    """Existing skill entries should be left untouched."""
    global_skills_dir = tmp_path / "global_skills"
    global_skills_dir.mkdir(parents=True)
    skill_dir = global_skills_dir / "skill-a"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: skill-a\n---\n", encoding="utf-8")
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_skills_dir",
        lambda: global_skills_dir,
    )

    team_workspace = tmp_path / "team_workspace"
    team_workspace.mkdir(parents=True)
    team_shared_skills = team_workspace / "skills"
    team_shared_skills.mkdir(parents=True)
    existing_skill = team_shared_skills / "skill-a"
    existing_skill.mkdir()
    (existing_skill / "SKILL.md").write_text("---\nname: existing-skill-a\n---\n", encoding="utf-8")

    spec = TeamAgentSpec.model_validate(
        {
            "team_name": "demo_team",
            "agents": {"leader": {}, "teammate": {}},
            "workspace": {"root_path": str(team_workspace), "enabled": True},
        }
    )

    manager = TeamManager()
    manager.ensure_team_shared_skills_initialized(spec)

    assert (team_shared_skills / "skill-a").resolve() == existing_skill.resolve()
    assert "existing-skill-a" in (team_shared_skills / "skill-a" / "SKILL.md").read_text(encoding="utf-8")


def test_member_configured_skills_linked_to_own_dir(tmp_path, monkeypatch):
    """Member-configured skills should be linked to member's own skills directory."""
    global_skills_dir = tmp_path / "global_skills"
    global_skills_dir.mkdir(parents=True)
    for skill_name in ("skill-a", "skill-b", "skill-c"):
        skill_dir = global_skills_dir / skill_name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\n", encoding="utf-8")
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_skills_dir",
        lambda: global_skills_dir,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_workspace_dir",
        lambda: tmp_path / "global_workspace",
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

    # Create team shared directory.
    team_workspace = tmp_path / "team_workspace"
    team_workspace.mkdir(parents=True)
    team_shared_skills = team_workspace / "skills"
    team_shared_skills.mkdir(parents=True)

    member_root = tmp_path / "member_workspace"
    member_root.mkdir(parents=True)
    (member_root / "skills").mkdir(parents=True)

    spec = TeamAgentSpec.model_validate(
        {
            "team_name": "demo_team",
            "agents": {
                "leader": {},
                "member_a": {"skills": ["skill-a"]},  # Only configure skill-a
            },
            "workspace": {"root_path": str(team_workspace), "enabled": True},
        }
    )
    TeamManager().ensure_team_shared_skills_initialized(spec)

    customizer = TeamManager.build_agent_customizer(
        spec=spec,
        deep_agent=type("_DeepAgent", (), {
            "deep_config": type("_Config", (), {"sys_operation": None})(),
            "ability_manager": type("_AbilityManager", (), {"list": lambda self: []})(),
        })(),
        session_id="session-1",
    )

    agent = type("_Agent", (), {
        "deep_config": type("_Config", (), {
            "workspace": type("_Workspace", (), {"root_path": str(member_root)})(),
            "sys_operation": None,
        })(),
        "ability_manager": type("_AbilityManager", (), {"list": lambda self: [], "add": lambda self, card: None})(),
        "card": type("_Card", (), {"id": "member_a", "name": "member"})(),
        "add_rail": lambda self, rail: None,
    })()

    customizer(agent, member_name="member_a", role="teammate")

    # Only member-configured skills are linked into the member workspace on spawn.
    member_skills_dir = member_root / "skills"
    assert member_skills_dir.is_dir()
    assert not member_skills_dir.is_symlink()
    _assert_link_points_to(member_skills_dir / "skill-a", global_skills_dir / "skill-a")
    assert not (member_skills_dir / "skill-b").exists()
    assert not (member_skills_dir / "skill-c").exists()

    assert not (member_skills_dir / "skills_state.json").exists()


def test_member_no_configured_skills_keeps_empty_link_view(tmp_path, monkeypatch):
    """When member has no configured skills, member directory does not link shared skills."""
    global_skills_dir = tmp_path / "global_skills"
    global_skills_dir.mkdir(parents=True)
    for skill_name in ("skill-a", "skill-b", "skill-c"):
        skill_dir = global_skills_dir / skill_name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\n", encoding="utf-8")
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_skills_dir",
        lambda: global_skills_dir,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_workspace_dir",
        lambda: tmp_path / "global_workspace",
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_runtime_inheritance.build_member_rails",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.plugins.rail_manager.get_rail_manager",
        lambda: type("_DummyRailManager", (), {
            "get_registered_rail_names": lambda self: [],
            "load_rail_instance_without_enabled_check": lambda self, name: None,
        })(),
    )

    # Create team shared directory
    team_workspace = tmp_path / "team_workspace"
    team_workspace.mkdir(parents=True)
    team_shared_skills = team_workspace / "skills"
    team_shared_skills.mkdir(parents=True)
    member_root = tmp_path / "member_workspace"
    member_root.mkdir(parents=True)
    (member_root / "skills").mkdir(parents=True)

    spec = TeamAgentSpec.model_validate(
        {
            "team_name": "demo_team",
            "agents": {
                "leader": {},
                "member_a": {},  # No skills configured
            },
            "workspace": {"root_path": str(team_workspace), "enabled": True},
        }
    )

    customizer = TeamManager.build_agent_customizer(
        spec=spec,
        deep_agent=type("_DeepAgent", (), {
            "deep_config": type("_Config", (), {"sys_operation": None})(),
            "ability_manager": type("_AbilityManager", (), {"list": lambda self: []})(),
        })(),
        session_id="session-1",
    )

    agent = type("_Agent", (), {
        "deep_config": type("_Config", (), {
            "workspace": type("_Workspace", (), {"root_path": str(member_root)})(),
            "sys_operation": None,
        })(),
        "ability_manager": type("_AbilityManager", (), {"list": lambda self: [], "add": lambda self, card: None})(),
        "card": type("_Card", (), {"id": "member_a", "name": "member"})(),
        "add_rail": lambda self, rail: None,
    })()

    customizer(agent, member_name="member_a", role="teammate")

    member_skills_dir = member_root / "skills"
    assert not (member_skills_dir / "skill-a").exists()
    assert not (member_skills_dir / "skill-b").exists()
    assert not (member_skills_dir / "skill-c").exists()
    assert not (member_skills_dir / "skills_state.json").exists()


def test_refresh_team_shared_skill_links_adds_new_global_skill(tmp_path, monkeypatch):
    """Refreshing shared links should add newly installed global skills."""
    global_skills_dir = tmp_path / "global_skills"
    global_skills_dir.mkdir(parents=True)
    skill_a = global_skills_dir / "skill-a"
    skill_a.mkdir()
    (skill_a / "SKILL.md").write_text("---\nname: skill-a\n---\n", encoding="utf-8")

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_skills_dir",
        lambda: global_skills_dir,
    )

    team_shared_skills = tmp_path / "team_workspace" / "skills"
    manager = TeamManager()
    manager.register_team_shared_skill_link_target("sess-1", team_shared_skills)

    assert manager.refresh_team_shared_skill_links("sess-1")
    _assert_link_points_to(team_shared_skills / "skill-a", skill_a)

    skill_b = global_skills_dir / "skill-b"
    skill_b.mkdir()
    (skill_b / "SKILL.md").write_text("---\nname: skill-b\n---\n", encoding="utf-8")

    assert manager.refresh_team_shared_skill_links("sess-1")
    _assert_link_points_to(team_shared_skills / "skill-b", skill_b)


def test_refresh_team_shared_skill_links_prunes_removed_global_skill(tmp_path, monkeypatch):
    """Refreshing shared links should remove links for uninstalled global skills."""
    global_skills_dir = tmp_path / "global_skills"
    global_skills_dir.mkdir(parents=True)
    skill_a = global_skills_dir / "skill-a"
    skill_a.mkdir()
    (skill_a / "SKILL.md").write_text("---\nname: skill-a\n---\n", encoding="utf-8")
    skill_b = global_skills_dir / "skill-b"
    skill_b.mkdir()
    (skill_b / "SKILL.md").write_text("---\nname: skill-b\n---\n", encoding="utf-8")

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_skills_dir",
        lambda: global_skills_dir,
    )

    team_shared_skills = tmp_path / "team_workspace" / "skills"
    manager = TeamManager()
    manager.register_team_shared_skill_link_target("sess-1", team_shared_skills)

    assert manager.refresh_team_shared_skill_links("sess-1")
    _assert_link_points_to(team_shared_skills / "skill-a", skill_a)
    _assert_link_points_to(team_shared_skills / "skill-b", skill_b)

    shutil.rmtree(skill_b)

    assert manager.refresh_team_shared_skill_links("sess-1")
    _assert_link_points_to(team_shared_skills / "skill-a", skill_a)
    assert not os.path.lexists(team_shared_skills / "skill-b")


def test_member_skill_tool_install_refresh_does_not_link_unselected_skill(tmp_path, monkeypatch):
    """Installing a skill should refresh team shared links without broadening member-selected links."""
    global_skills_dir = tmp_path / "global_skills"
    global_skills_dir.mkdir(parents=True)
    skill_a = global_skills_dir / "skill-a"
    skill_a.mkdir()
    (skill_a / "SKILL.md").write_text("---\nname: skill-a\n---\n", encoding="utf-8")

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_skills_dir",
        lambda: global_skills_dir,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_workspace_dir",
        lambda: tmp_path / "global_workspace",
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

    team_workspace = tmp_path / "team_workspace"
    team_workspace.mkdir(parents=True)
    spec = TeamAgentSpec.model_validate(
        {
            "team_name": "demo_team",
            "agents": {
                "leader": {},
                "member_a": {"skills": ["skill-a"]},
            },
            "workspace": {"root_path": str(team_workspace), "enabled": True},
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
    )

    rails = []
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
            "ability_manager": type("_AbilityManager", (), {"list": lambda self: [], "add": lambda self, card: None})(),
            "card": type("_Card", (), {"id": "member_a", "name": "member"})(),
            "add_rail": lambda self, rail: rails.append(rail),
        },
    )()

    customizer(agent, member_name="member_a", role="teammate")
    member_rail = next(rail for rail in rails if isinstance(rail, MemberSkillToolkitRail))
    skill_b = global_skills_dir / "skill-b"
    skill_b.mkdir()
    (skill_b / "SKILL.md").write_text("---\nname: skill-b\n---\n", encoding="utf-8")

    member_rail._refresh_links({"success": True, "skill": {"name": "skill-b"}})

    member_skills_dir = member_root / "skills"
    team_shared_skills = team_workspace / "skills"
    _assert_link_points_to(member_skills_dir / "skill-a", skill_a)
    assert not os.path.lexists(member_skills_dir / "skill-b")
    _assert_link_points_to(team_shared_skills / "skill-b", skill_b)


def test_member_skill_tool_uninstall_removes_member_link(tmp_path, monkeypatch):
    """Uninstalling a selected skill should remove the member's stale skill link."""
    global_skills_dir = tmp_path / "global_skills"
    global_skills_dir.mkdir(parents=True)
    skill_a = global_skills_dir / "skill-a"
    skill_a.mkdir()
    (skill_a / "SKILL.md").write_text("---\nname: skill-a\n---\n", encoding="utf-8")

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_skills_dir",
        lambda: global_skills_dir,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_workspace_dir",
        lambda: tmp_path / "global_workspace",
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

    team_workspace = tmp_path / "team_workspace"
    team_workspace.mkdir(parents=True)
    spec = TeamAgentSpec.model_validate(
        {
            "team_name": "demo_team",
            "agents": {
                "leader": {},
                "member_a": {"skills": ["skill-a"]},
            },
            "workspace": {"root_path": str(team_workspace), "enabled": True},
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
        session_id="session-2",
    )

    rails = []
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
            "ability_manager": type("_AbilityManager", (), {"list": lambda self: [], "add": lambda self, card: None})(),
            "card": type("_Card", (), {"id": "member_a", "name": "member"})(),
            "add_rail": lambda self, rail: rails.append(rail),
        },
    )()

    customizer(agent, member_name="member_a", role="teammate")
    member_skills_dir = member_root / "skills"
    _assert_link_points_to(member_skills_dir / "skill-a", skill_a)

    shutil.rmtree(skill_a)
    member_rail = next(rail for rail in rails if isinstance(rail, MemberSkillToolkitRail))
    member_rail._refresh_links({"success": True, "removed": True, "name": "skill-a"})

    assert not os.path.lexists(member_skills_dir / "skill-a")
    assert not os.path.lexists(team_workspace / "skills" / "skill-a")


def test_member_configured_skill_sync_prunes_unselected_link(tmp_path, monkeypatch):
    """Member initialization should remove stale unselected skill links only when they are links."""
    global_skills_dir = tmp_path / "global_skills"
    global_skills_dir.mkdir(parents=True)
    for skill_name in ("skill-a", "skill-b"):
        skill_dir = global_skills_dir / skill_name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\n", encoding="utf-8")

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_skills_dir",
        lambda: global_skills_dir,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_agent_workspace_dir",
        lambda: tmp_path / "global_workspace",
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

    team_workspace = tmp_path / "team_workspace"
    team_workspace.mkdir(parents=True)
    member_root = tmp_path / "member_workspace"
    member_skills_dir = member_root / "skills"
    member_skills_dir.mkdir(parents=True)
    link_skill_dir(global_skills_dir / "skill-b", member_skills_dir / "skill-b")

    spec = TeamAgentSpec.model_validate(
        {
            "team_name": "demo_team",
            "agents": {
                "leader": {},
                "member_a": {"skills": ["skill-a"]},
            },
            "workspace": {"root_path": str(team_workspace), "enabled": True},
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
        session_id="session-3",
    )
    agent = type(
        "_Agent",
        (),
        {
            "deep_config": type(
                "_Config",
                (),
                {"workspace": type("_Workspace", (), {"root_path": str(member_root)})(), "sys_operation": None},
            )(),
            "ability_manager": type("_AbilityManager", (), {"list": lambda self: [], "add": lambda self, card: None})(),
            "card": type("_Card", (), {"id": "member_a", "name": "member"})(),
            "add_rail": lambda self, rail: None,
        },
    )()

    customizer(agent, member_name="member_a", role="teammate")

    _assert_link_points_to(member_skills_dir / "skill-a", global_skills_dir / "skill-a")
    assert not os.path.lexists(member_skills_dir / "skill-b")


def test_remove_skill_dir_link_keeps_ordinary_directory(tmp_path):
    """Removing a skill link should not delete ordinary directories."""
    ordinary_skill_dir = tmp_path / "ordinary-skill"
    ordinary_skill_dir.mkdir()
    (ordinary_skill_dir / "SKILL.md").write_text("---\nname: ordinary-skill\n---\n", encoding="utf-8")

    remove_skill_dir_link(ordinary_skill_dir)

    assert ordinary_skill_dir.is_dir()
    assert (ordinary_skill_dir / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_team_shared_skill_link_refresh_rail_refreshes_after_global_skill_write(tmp_path, monkeypatch):
    """The after-tool rail should refresh only when write tools touch global skills."""
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.rails.team_shared_skill_link_refresh_rail.get_cwd",
        lambda: str(tmp_path),
    )
    global_skills_dir = tmp_path / "global_skills"
    skill_dir = global_skills_dir / "skill-a"
    skill_dir.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("---\nname: skill-a\n---\n", encoding="utf-8")
    refresh_calls = []

    rail = TeamSharedSkillLinkRefreshRail(
        global_skills_dir=global_skills_dir,
        refresh_links=lambda: refresh_calls.append("refresh"),
    )
    ctx = type(
        "_Ctx",
        (),
        {
            "inputs": ToolCallInputs(
                tool_name="write_file",
                tool_args={"file_path": str(skill_md.relative_to(tmp_path))},
            )
        },
    )()

    await rail.after_tool_call(ctx)

    assert refresh_calls == ["refresh"]
