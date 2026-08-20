# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from jiuwenclaw.agentserver.swarm import enrich_team_spec_for_swarm
from jiuwenclaw.agentserver.swarm.registry import PLATFORM_MEMBER_RAILS
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, TeamAgentSpec


def test_enrich_team_spec_for_swarm_sets_build_context_and_rail_spec() -> None:
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec()},
        team_name="unit-team",
        spawn_mode="inprocess",
        enable_permissions=True,
    )
    enrich_team_spec_for_swarm(
        spec,
        session_id="sess-1",
        mode="team",
        channel_id="cli",
    )

    assert spec.build_context is not None
    assert spec.build_context_seed
    assert spec.build_context.team_id == "unit-team"
    assert spec.build_context.session_id == "sess-1"

    leader_rails = [r.type for r in (spec.agents["leader"].rails or [])]
    teammate_rails = [r.type for r in (spec.agents["teammate"].rails or [])]
    assert leader_rails == [PLATFORM_MEMBER_RAILS]
    assert teammate_rails == [PLATFORM_MEMBER_RAILS]
    leader_params = spec.agents["leader"].rails[0].params
    assert leader_params.get("enable_permissions") is True


def test_enrich_adds_missing_teammate_template_before_rewrite() -> None:
    """Presets that only ship agents.leader must still enrich teammate rails."""
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="leader-only-team",
        spawn_mode="inprocess",
    )
    enrich_team_spec_for_swarm(
        spec,
        session_id="sess-leader-only",
        mode="team",
        channel_id="cli",
    )
    assert "teammate" in spec.agents
    teammate_rails = [r.type for r in (spec.agents["teammate"].rails or [])]
    assert teammate_rails == [PLATFORM_MEMBER_RAILS]


def test_enrich_named_predefined_members_get_platform_rails() -> None:
    """OfficeClaw presets key agents by member name; resolve prefers that key."""
    spec = TeamAgentSpec(
        agents={
            "leader": DeepAgentSpec(skills=["task-implement", "handoff"]),
            "product-architect": DeepAgentSpec(skills=["docx-craft", "handoff"]),
            "client-engineer": DeepAgentSpec(skills=["caveman"]),
            "teammate": DeepAgentSpec(skills=["should-not-leak"]),
        },
        team_name="oc_team_preset-software-dev",
        spawn_mode="inprocess",
    )
    enrich_team_spec_for_swarm(
        spec,
        session_id="sess-dev",
        mode="team",
        channel_id="cli",
    )

    for key in ("leader", "teammate", "product-architect", "client-engineer"):
        types = [r.type for r in (spec.agents[key].rails or [])]
        assert PLATFORM_MEMBER_RAILS in types, key

    skills_map = spec.build_context.agent_skills_by_key
    assert skills_map["product-architect"] == ["docx-craft", "handoff"]
    assert skills_map["teammate"] == ["should-not-leak"]
    assert skills_map["leader"] == ["task-implement", "handoff"]


def test_resolve_member_skills_does_not_borrow_teammate_template() -> None:
    from jiuwenclaw.agentserver.swarm.context import SwarmBuildContext
    from jiuwenclaw.agentserver.swarm.providers.member_rails import (
        _resolve_member_enabled_skills,
    )

    ctx = SwarmBuildContext(
        agent_skills_by_key={
            "teammate": ["should-not-leak"],
            "product-architect": ["docx-craft"],
            "leader": ["task-implement"],
        },
    )
    assert _resolve_member_enabled_skills(
        ctx, member_name="product-architect", role="teammate"
    ) == ["docx-craft"]
    # Named member with no own skills must not inherit teammate template skills.
    assert (
        _resolve_member_enabled_skills(
            ctx, member_name="server-engineer", role="teammate"
        )
        is None
    )
    assert _resolve_member_enabled_skills(
        ctx, member_name="chief-researcher", role="leader"
    ) == ["task-implement"]
    assert _resolve_member_enabled_skills(
        ctx, member_name="teammate", role="teammate"
    ) == ["should-not-leak"]


@pytest.mark.asyncio
async def test_team_manager_get_swarm_enriched_team_spec_calls_enrich(monkeypatch) -> None:
    from jiuwenclaw.agentserver.team.team_manager import TeamManager

    called: list[str] = []

    def _fake_enrich(spec, **kwargs):
        called.append(kwargs.get("session_id", ""))
        spec.build_context_seed = {"session_id": kwargs.get("session_id")}

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.swarm.enrich_team_spec_for_swarm",
        _fake_enrich,
    )
    manager = TeamManager()

    async def _noop_pg(_cfg):
        return None

    monkeypatch.setattr(manager, "_ensure_postgresql_for_leader", _noop_pg)
    monkeypatch.setattr(
        manager,
        "_load_team_spec",
        lambda _sid, **_: TeamAgentSpec(
            agents={"leader": DeepAgentSpec()},
            team_name="t",
            spawn_mode="inprocess",
        ),
    )
    monkeypatch.setattr(manager, "_apply_session_scoped_team_name", lambda *a, **k: None)

    await manager.get_swarm_enriched_team_spec("sess-x", mode="team")
    assert called == ["sess-x"]
