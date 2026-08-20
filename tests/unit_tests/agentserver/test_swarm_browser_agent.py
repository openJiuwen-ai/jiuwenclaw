# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenclaw.agentserver.swarm import enrich_team_spec_for_swarm
from jiuwenclaw.agentserver.swarm.providers.browser_subagent import (
    SWARM_BROWSER_AGENT,
    _browser_key,
    build_swarm_browser_agent,
)
from jiuwenclaw.agentserver.swarm.registry import PLATFORM_MEMBER_RAILS, register_swarm_providers
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, TeamAgentSpec
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.deep_agent_spec import SubAgentSpec


def test_browser_key_prefers_member_name_over_role() -> None:
    assert _browser_key("sess", "search-a", "teammate") == "sess-search-a"
    assert _browser_key("sess", "", "leader") == "sess-leader"
    assert _browser_key("", "search-a", "teammate") == "search-a"
    assert _browser_key("", "", "") == ""


def test_enrich_mounts_swarm_browser_agent_when_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.swarm.assembly.get_config",
        lambda: {
            "react": {
                "subagents": {"browser_agent": {"enabled": True, "max_iterations": 9}},
                "max_iterations": 100,
            },
            "preferred_language": "zh",
        },
    )
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec()},
        team_name="unit-team-browser",
        spawn_mode="inprocess",
    )
    enrich_team_spec_for_swarm(spec, session_id="sess-b", mode="team", channel_id="cli")

    for role in ("leader", "teammate"):
        subagents = list(spec.agents[role].subagents or [])
        assert len(subagents) == 1
        sa = subagents[0]
        assert isinstance(sa, SubAgentSpec)
        assert sa.factory_name == SWARM_BROWSER_AGENT
        assert sa.agent_card.name == "browser_agent"
        assert sa.factory_kwargs.get("max_iterations") == 9
        assert sa.factory_kwargs.get("language") == "cn"

    leader_rails = [r.type for r in (spec.agents["leader"].rails or [])]
    assert PLATFORM_MEMBER_RAILS in leader_rails


def test_enrich_skips_browser_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.swarm.assembly.get_config",
        lambda: {"react": {"subagents": {"browser_agent": {"enabled": False}}}},
    )
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="unit-team-no-browser",
        spawn_mode="inprocess",
    )
    enrich_team_spec_for_swarm(spec, session_id="sess-n", mode="team")
    assert list(spec.agents["leader"].subagents or []) == []


def test_build_swarm_browser_agent_passes_browser_key(monkeypatch) -> None:
    register_swarm_providers()
    captured: dict = {}

    def _fake_build(model, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            factory_kwargs={"settings": object()},
            agent_card=AgentCard(name="browser_agent"),
        )

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.swarm.providers.browser_subagent.build_browser_agent_config",
        _fake_build,
    )

    from jiuwenclaw.agentserver.swarm.context import SwarmBuildContext

    ctx = SwarmBuildContext(
        session_id="s1",
        language="cn",
        member_name="research-1",
        role="teammate",
    )
    ctx.extras["_parent_model"] = object()

    out = build_swarm_browser_agent({"max_iterations": 7}, ctx)
    assert out is not None
    assert captured.get("browser_key") == "s1-research-1"
    assert captured.get("language") == "cn"
    assert captured.get("max_iterations") == 7
