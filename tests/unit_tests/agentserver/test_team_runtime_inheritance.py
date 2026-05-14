# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team runtime inheritance helpers."""

from types import SimpleNamespace
from unittest.mock import patch

from openjiuwen.core.foundation.tool import ToolCard

from jiuwenclaw.agents.harness.team.team_runtime_inheritance import (
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
                _make_tool_card("send_file_to_user"),
            ]
        )
    )

    inherited = filter_inheritable_ability_cards(main_agent)
    inherited_names = {card.name for card in inherited}

    assert "visual_question_answering" in inherited_names
    assert "audio_question_answering" in inherited_names
    assert "audio_metadata" in inherited_names
    assert "user_todos" in inherited_names
    assert "task_tool" in inherited_names
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


def test_build_member_rails_accepts_team_workspace_info(tmp_path):
    team_workspace = TeamWorkspaceInfo(
        skills_dir=str(tmp_path / "skills"),
        trajectories_dir=str(tmp_path / "trajectories"),
        team_id="demo-team",
    )

    with patch(
            "jiuwenclaw.agents.harness.team.team_runtime_inheritance.FileTrajectoryStore",
            return_value=object(),
    ):
        rails = build_member_rails(
            member_info=SimpleNamespace(agent_name="leader", model_name="demo-model", role="leader"),
            runtime=SimpleNamespace(channel="web", language="cn"),
            team_workspace=TeamWorkspaceInfo(
                root_dir=str(tmp_path / "team-workspace"),
                skills_dir=team_workspace.skills_dir,
                trajectories_dir=team_workspace.trajectories_dir,
                team_id=team_workspace.team_id,
                config={"evolution": {"skill_create": False}},
            ),
        )

    assert isinstance(rails, list)


def test_build_member_rails_skips_team_skill_rail_when_auto_scan_disabled(
    tmp_path, monkeypatch
):
    class _FakeTeamSkillRail:
        pass

    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_runtime_inheritance.TeamSkillRail",
        _FakeTeamSkillRail,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_runtime_inheritance.build_evolution_llm",
        lambda config=None: (object(), "model"),
    )

    rails = build_member_rails(
        member_info=MemberInfo(role="leader"),
        team_workspace=TeamWorkspaceInfo(
            skills_dir=str(tmp_path / "skills"),
            config={"evolution": {"enabled": True, "auto_scan": False}},
        ),
    )

    assert not any(isinstance(rail, _FakeTeamSkillRail) for rail in rails)


def test_build_member_rails_creates_team_skill_rail_when_auto_scan_enabled(
    tmp_path, monkeypatch
):
    class _FakeTeamSkillRail:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.delenv("EVOLUTION_AUTO_SCAN", raising=False)
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_runtime_inheritance.TeamSkillRail",
        _FakeTeamSkillRail,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_runtime_inheritance.build_evolution_llm",
        lambda config=None: (object(), "model"),
    )

    rails = build_member_rails(
        member_info=MemberInfo(role="leader"),
        team_workspace=TeamWorkspaceInfo(
            skills_dir=str(tmp_path / "skills"),
            config={"evolution": {"auto_scan": True}},
        ),
    )

    assert any(isinstance(rail, _FakeTeamSkillRail) for rail in rails)


def test_build_member_rails_reads_react_evolution_auto_scan(
    tmp_path, monkeypatch
):
    class _FakeTeamSkillRail:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.delenv("EVOLUTION_AUTO_SCAN", raising=False)
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_runtime_inheritance.TeamSkillRail",
        _FakeTeamSkillRail,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_runtime_inheritance.build_evolution_llm",
        lambda config=None: (object(), "model"),
    )

    rails = build_member_rails(
        member_info=MemberInfo(role="leader"),
        team_workspace=TeamWorkspaceInfo(
            skills_dir=str(tmp_path / "skills"),
            config={"react": {"evolution": {"auto_scan": True}}},
        ),
    )

    assert any(isinstance(rail, _FakeTeamSkillRail) for rail in rails)


def test_build_member_rails_env_auto_scan_overrides_config(tmp_path, monkeypatch):
    class _FakeTeamSkillRail:
        pass

    monkeypatch.setenv("EVOLUTION_AUTO_SCAN", "false")
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_runtime_inheritance.TeamSkillRail",
        _FakeTeamSkillRail,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_runtime_inheritance.build_evolution_llm",
        lambda config=None: (object(), "model"),
    )

    rails = build_member_rails(
        member_info=MemberInfo(role="leader"),
        team_workspace=TeamWorkspaceInfo(
            skills_dir=str(tmp_path / "skills"),
            config={"evolution": {"auto_scan": True}},
        ),
    )

    assert not any(isinstance(rail, _FakeTeamSkillRail) for rail in rails)


def test_build_member_rails_keeps_member_skill_evolution_when_auto_scan_disabled(
    tmp_path, monkeypatch
):
    class _FakeSkillEvolutionRail:
        def __init__(self, **kwargs):
            self.auto_scan = kwargs["auto_scan"]

    monkeypatch.delenv("EVOLUTION_AUTO_SCAN", raising=False)
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_runtime_inheritance.SkillEvolutionRail",
        _FakeSkillEvolutionRail,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_runtime_inheritance.build_evolution_llm",
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
        "jiuwenclaw.agents.harness.team.team_runtime_inheritance.TeamSkillCreateRail",
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
        "jiuwenclaw.agents.harness.team.team_runtime_inheritance.TeamSkillCreateRail",
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
        "jiuwenclaw.agents.harness.team.team_runtime_inheritance.TeamSkillCreateRail",
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
