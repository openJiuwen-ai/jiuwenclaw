# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team runtime inheritance helpers."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openjiuwen.core.foundation.tool import ToolCard

from jiuwenavatar.agents.harness.team.team_runtime_inheritance import (
    MemberInfo,
    RuntimeInfo,
    TeamWorkspaceInfo,
    build_evolution_llm,
    build_member_rails,
    build_skill_evolution_rail,
    filter_inheritable_ability_cards,
    resolve_model_config,
)


class _FakeAbilityManager:
    def __init__(self, abilities):
        self._abilities = abilities

    def list(self):
        return list(self._abilities)


def _make_tool_card(name: str) -> ToolCard:
    return ToolCard(
        id=name,
        name=name,
        description=f"{name} description",
        input_params={"type": "object"},
    )


def test_filter_inheritable_ability_cards_includes_extended_claw_tools():
    main_agent = SimpleNamespace(
        ability_manager=_FakeAbilityManager(
            [
                _make_tool_card("visual_question_answering"),
                _make_tool_card("audio_question_answering"),
                _make_tool_card("audio_metadata"),
                _make_tool_card("user_todos"),
                _make_tool_card("task_tool"),
                _make_tool_card("acp_chat"),
                _make_tool_card("send_file_to_user"),
            ]
        )
    )

    inherited = filter_inheritable_ability_cards(main_agent)
    inherited_names = {card.name for card in inherited}

    assert "acp_chat" in inherited_names
    assert "visual_question_answering" in inherited_names
    assert "audio_question_answering" in inherited_names
    assert "audio_metadata" in inherited_names
    assert "user_todos" in inherited_names
    assert "task_tool" not in inherited_names
    assert "send_file_to_user" not in inherited_names


# -- resolve_model_config tests --

def test_resolve_model_config_from_default():
    config = {
        "models": {
            "default": {
                "model_client_config": {"model_name": "test-model", "api_key": "k1"},
                "model_config_obj": {"temperature": 0.5},
            }
        },
        "react": {},
    }
    client_cfg, model_cfg_obj, model_name = resolve_model_config(config)
    assert client_cfg == {"model_name": "test-model", "api_key": "k1"}
    assert model_cfg_obj == {"temperature": 0.5}
    assert model_name == "test-model"


def test_resolve_model_config_fallback_to_react():
    config = {
        "models": {"default": {}},
        "react": {
            "model_client_config": {"model_name": "react-model"},
            "model_config_obj": {"temperature": 0.3},
        },
    }
    client_cfg, model_cfg_obj, model_name = resolve_model_config(config)
    assert client_cfg == {"model_name": "react-model"}
    assert model_cfg_obj == {"temperature": 0.3}
    assert model_name == "react-model"


def test_resolve_model_config_default_name():
    config = {"models": {"default": {}}, "react": {}}
    client_cfg, model_cfg_obj, model_name = resolve_model_config(config)
    assert client_cfg == {}
    assert model_cfg_obj == {}
    assert model_name == "gpt-4"


# -- build_evolution_llm tests --

def test_build_evolution_llm_from_config():
    config = {
        "models": {
            "default": {
                "model_client_config": {"model_name": "test-model"},
                "model_config_obj": {"temperature": 0.5},
            }
        },
        "react": {},
    }
    fake_model = object()
    with patch("openjiuwen.core.foundation.llm.ModelClientConfig", return_value=None), \
            patch("openjiuwen.core.foundation.llm.ModelRequestConfig"), \
            patch("openjiuwen.core.foundation.llm.Model", return_value=fake_model):
        model, model_name = build_evolution_llm(config)

    assert model_name == "test-model"
    assert model is fake_model


def test_build_evolution_llm_fallback_to_react_config():
    config = {
        "models": {"default": {}},
        "react": {
            "model_client_config": {"model_name": "react-model"},
        },
    }
    fake_model = object()
    with patch("openjiuwen.core.foundation.llm.ModelClientConfig", return_value=None), \
            patch("openjiuwen.core.foundation.llm.ModelRequestConfig"), \
            patch("openjiuwen.core.foundation.llm.Model", return_value=fake_model):
        model, model_name = build_evolution_llm(config)

    assert model_name == "react-model"


def test_build_evolution_llm_default_model_name():
    config = {"models": {"default": {}}, "react": {}}
    fake_model = object()
    with patch("openjiuwen.core.foundation.llm.ModelClientConfig", return_value=None), \
            patch("openjiuwen.core.foundation.llm.ModelRequestConfig"), \
            patch("openjiuwen.core.foundation.llm.Model", return_value=fake_model):
        model, model_name = build_evolution_llm(config)

    assert model_name == "gpt-4"


# -- build_skill_evolution_rail tests --

def test_build_skill_evolution_rail_returns_none_on_invalid_config(tmp_path):
    """When config is invalid for Model construction, should return None."""
    result = build_skill_evolution_rail(
        skills_dir=str(tmp_path / "nonexistent"),
        config={
            "models": {"default": {}},
            "react": {},
        },
    )
    # Will fail due to empty model_client_config, returning None
    assert result is None


def test_build_member_rails_wires_team_trajectory_registry_to_evolution_rails(
    tmp_path,
    monkeypatch,
):
    class _FakeTeamSkillEvolutionRail:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _FakeSkillEvolutionRail:
        def __init__(self, **kwargs):
            self.bound_sink = None
            self.bound_team_id = None
            self.bound_member_role = None

        def set_trajectory_sink(self, sink, *, team_id, member_role):
            self.bound_sink = sink
            self.bound_team_id = team_id
            self.bound_member_role = member_role

    registry = object()
    monkeypatch.delenv("EVOLUTION_AUTO_SCAN", raising=False)
    monkeypatch.setattr(
        "jiuwenavatar.agents.harness.team.team_runtime_inheritance.TeamSkillEvolutionRail",
        _FakeTeamSkillEvolutionRail,
    )
    monkeypatch.setattr(
        "jiuwenavatar.agents.harness.team.team_runtime_inheritance.build_evolution_llm",
        lambda config=None: (object(), "model"),
    )
    monkeypatch.setattr(
        "jiuwenavatar.agents.harness.team.team_runtime_inheritance.SkillEvolutionRail",
        _FakeSkillEvolutionRail,
    )

    leader_rails = build_member_rails(
        member_info=MemberInfo(role="leader"),
        team_workspace=TeamWorkspaceInfo(
            root_dir=str(tmp_path / "team-workspace"),
            skills_dir=str(tmp_path / "skills"),
            team_id="demo-team",
            trajectory_registry=registry,
            config={"evolution": {"auto_scan": True}},
        ),
    )
    member_rails = build_member_rails(
        member_info=MemberInfo(role="teammate"),
        team_workspace=TeamWorkspaceInfo(
            root_dir=str(tmp_path / "team-workspace"),
            skills_dir=str(tmp_path / "skills"),
            team_id="demo-team",
            trajectory_registry=registry,
            config={"evolution": {"auto_scan": True}},
        ),
    )

    leader_rail = next(
        rail for rail in leader_rails if isinstance(rail, _FakeTeamSkillEvolutionRail)
    )
    member_rail = next(
        rail for rail in member_rails if isinstance(rail, _FakeSkillEvolutionRail)
    )

    assert leader_rail.kwargs["trajectory_source"] is registry
    assert leader_rail.kwargs["trajectory_sink"] is registry
    assert leader_rail.kwargs["member_role"] == "leader"
    assert member_rail.bound_sink is registry
    assert member_rail.bound_team_id == "demo-team"
    assert member_rail.bound_member_role == "teammate"


@pytest.mark.parametrize(
    ("env_auto_scan", "config", "expected_auto_scan"),
    [
        (None, {"evolution": {"enabled": True, "auto_scan": False}}, False),
        (None, {"evolution": {"enabled": False, "auto_scan": False}}, False),
        (None, {"react": {"evolution": {"enabled": True, "auto_scan": True}}}, True),
        ("false", {"evolution": {"enabled": True, "auto_scan": True}}, False),
    ],
)
def test_build_member_rails_creates_leader_team_skill_evolution_rail_with_auto_scan(
    tmp_path,
    monkeypatch,
    env_auto_scan,
    config,
    expected_auto_scan,
):
    class _FakeTeamSkillEvolutionRail:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    if env_auto_scan is None:
        monkeypatch.delenv("EVOLUTION_AUTO_SCAN", raising=False)
    else:
        monkeypatch.setenv("EVOLUTION_AUTO_SCAN", env_auto_scan)
    monkeypatch.setattr(
        "jiuwenavatar.agents.harness.team.team_runtime_inheritance.TeamSkillEvolutionRail",
        _FakeTeamSkillEvolutionRail,
    )
    monkeypatch.setattr(
        "jiuwenavatar.agents.harness.team.team_runtime_inheritance.build_evolution_llm",
        lambda config=None: (object(), "model"),
    )

    rails = build_member_rails(
        member_info=MemberInfo(role="leader"),
        team_workspace=TeamWorkspaceInfo(
            skills_dir=str(tmp_path / "skills"),
            config=config,
        ),
    )

    team_skill_rails = [rail for rail in rails if isinstance(rail, _FakeTeamSkillEvolutionRail)]
    assert len(team_skill_rails) == 1
    assert team_skill_rails[0].kwargs["auto_scan"] is expected_auto_scan


def test_build_member_rails_keeps_member_skill_evolution_when_auto_scan_disabled(
    tmp_path, monkeypatch
):
    class _FakeSkillEvolutionRail:
        def __init__(self, **kwargs):
            self.auto_scan = kwargs["auto_scan"]

    monkeypatch.delenv("EVOLUTION_AUTO_SCAN", raising=False)
    monkeypatch.setattr(
        "jiuwenavatar.agents.harness.team.team_runtime_inheritance.SkillEvolutionRail",
        _FakeSkillEvolutionRail,
    )
    monkeypatch.setattr(
        "jiuwenavatar.agents.harness.team.team_runtime_inheritance.build_evolution_llm",
        lambda config=None: (object(), "model"),
    )

    rails = build_member_rails(
        member_info=MemberInfo(role="member"),
        runtime=RuntimeInfo(channel="web"),
        team_workspace=TeamWorkspaceInfo(
            skills_dir=str(tmp_path / "skills"),
            config={"evolution": {"auto_scan": False}},
        ),
    )

    evo_rails = [rail for rail in rails if isinstance(rail, _FakeSkillEvolutionRail)]
    assert len(evo_rails) == 1
    assert evo_rails[0].auto_scan is False


def test_build_member_rails_keeps_team_skill_create_when_auto_scan_disabled(
    tmp_path, monkeypatch
):
    class _FakeTeamSkillCreateRail:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.delenv("SKILL_CREATE", raising=False)
    monkeypatch.setattr(
        "jiuwenavatar.agents.harness.team.team_runtime_inheritance.TeamSkillCreateRail",
        _FakeTeamSkillCreateRail,
    )

    rails = build_member_rails(
        member_info=MemberInfo(role="leader"),
        team_workspace=TeamWorkspaceInfo(
            skills_dir=str(tmp_path / "skills"),
            config={"evolution": {"auto_scan": False, "skill_create": True}},
        ),
    )

    assert any(isinstance(rail, _FakeTeamSkillCreateRail) for rail in rails)


def test_build_member_rails_reads_react_evolution_skill_create(
    tmp_path, monkeypatch
):
    class _FakeTeamSkillCreateRail:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.delenv("SKILL_CREATE", raising=False)
    monkeypatch.setattr(
        "jiuwenavatar.agents.harness.team.team_runtime_inheritance.TeamSkillCreateRail",
        _FakeTeamSkillCreateRail,
    )

    rails = build_member_rails(
        member_info=MemberInfo(role="leader"),
        team_workspace=TeamWorkspaceInfo(
            skills_dir=str(tmp_path / "skills"),
            config={"react": {"evolution": {"skill_create": True}}},
        ),
    )

    assert any(isinstance(rail, _FakeTeamSkillCreateRail) for rail in rails)


def test_build_member_rails_env_skill_create_overrides_config(tmp_path, monkeypatch):
    class _FakeTeamSkillCreateRail:
        pass

    monkeypatch.setenv("SKILL_CREATE", "false")
    monkeypatch.setattr(
        "jiuwenavatar.agents.harness.team.team_runtime_inheritance.TeamSkillCreateRail",
        _FakeTeamSkillCreateRail,
    )

    rails = build_member_rails(
        member_info=MemberInfo(role="leader"),
        team_workspace=TeamWorkspaceInfo(
            skills_dir=str(tmp_path / "skills"),
            config={"evolution": {"skill_create": True}},
        ),
    )

    assert not any(isinstance(rail, _FakeTeamSkillCreateRail) for rail in rails)
