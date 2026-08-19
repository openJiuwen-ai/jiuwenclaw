# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team member rails: SysOperation declarative-only; leader ask_user_question."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jiuwenclaw.agentserver.team.team_runtime_inheritance import (
    MemberInfo,
    RuntimeInfo,
    TeamWorkspaceInfo,
    build_member_rails,
)


def test_build_member_rails_omits_imperative_sys_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dual-mount fix: no bare SysOperationRail(); configurator owns core.sys_operation."""

    class _FakeRail:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.RuntimePromptRail",
        lambda **kwargs: _FakeRail(name="RuntimePromptRail", **kwargs),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.ResponsePromptRail",
        lambda **kwargs: _FakeRail(name="ResponsePromptRail", **kwargs),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.JiuSwarmStreamEventRail",
        lambda **kwargs: _FakeRail(name="JiuSwarmStreamEventRail", **kwargs),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.SecurityRail",
        lambda **kwargs: _FakeRail(name="SecurityRail", **kwargs),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.AvatarPromptRail",
        lambda **kwargs: _FakeRail(name="AvatarPromptRail", **kwargs),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.HeartbeatRail",
        None,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.get_context_engine_enabled",
        lambda config: False,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance._build_team_skill_rails",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance._build_team_disabled_tools_rail",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.rails.ask_user_question_tool_rail.AskUserQuestionToolRail",
        lambda: _FakeRail(name="AskUserQuestionToolRail"),
    )

    rails = build_member_rails(
        member_info=MemberInfo(agent_name="office", role="leader"),
        runtime=RuntimeInfo(channel="web", language="cn"),
        team_workspace=TeamWorkspaceInfo(root_dir=None, config={}),
    )
    type_names = []
    for r in rails:
        if isinstance(r, _FakeRail):
            type_names.append(r.kwargs.get("name") or (r.args[0] if r.args else type(r).__name__))
        else:
            type_names.append(type(r).__name__)

    assert "AskUserQuestionToolRail" in type_names
    assert "SysOperationRail" not in type_names
    assert not any("SysOperation" in str(n) for n in type_names)


def test_teammate_skips_ask_user_question_rail(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeRail:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.RuntimePromptRail",
        lambda **kwargs: _FakeRail(name="RuntimePromptRail", **kwargs),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.ResponsePromptRail",
        lambda **kwargs: _FakeRail(name="ResponsePromptRail", **kwargs),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.JiuSwarmStreamEventRail",
        lambda **kwargs: _FakeRail(name="JiuSwarmStreamEventRail", **kwargs),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.SecurityRail",
        lambda **kwargs: _FakeRail(name="SecurityRail", **kwargs),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.AvatarPromptRail",
        lambda **kwargs: _FakeRail(name="AvatarPromptRail", **kwargs),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.HeartbeatRail",
        None,
    )
    monkeypatch.setattr(
        "openjiuwen.harness.rails.task_planning_rail.TaskPlanningRail",
        lambda **kwargs: _FakeRail(name="TaskPlanningRail", **kwargs),
    )
    monkeypatch.setattr(
        "openjiuwen.harness.rails.task_planning_rail.resolve_task_planning_rail_kwargs",
        lambda react_cfg: {},
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance.get_context_engine_enabled",
        lambda config: False,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance._build_team_skill_rails",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.team_runtime_inheritance._build_team_disabled_tools_rail",
        lambda *a, **k: None,
    )

    ask_calls = []

    def _ask_rail():
        ask_calls.append(1)
        return _FakeRail(name="AskUserQuestionToolRail")

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.rails.ask_user_question_tool_rail.AskUserQuestionToolRail",
        _ask_rail,
    )

    rails = build_member_rails(
        member_info=MemberInfo(agent_name="worker", role="teammate"),
        runtime=RuntimeInfo(channel="web", language="cn"),
        team_workspace=TeamWorkspaceInfo(root_dir=None, config={}),
    )
    type_names = [
        r.kwargs.get("name") if isinstance(r, _FakeRail) else type(r).__name__ for r in rails
    ]
    assert ask_calls == []
    assert "AskUserQuestionToolRail" not in type_names
