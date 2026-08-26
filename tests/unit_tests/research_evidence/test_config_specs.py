from __future__ import annotations

from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec

from jiuwenswarm.agents.swarm import registry
from jiuwenswarm.agents.swarm.config_specs import build_member_deep_agent_spec


def test_team_spec_contains_opt_in_research_evidence_params():
    config = {
        "research_evidence": {
            "enabled": True,
            "token_budget": 1536,
            "min_reliability": 0.6,
            "required_kinds": ["literature", "experiment"],
        }
    }
    spec = build_member_deep_agent_spec(
        config,
        "team",
        "leader",
        DeepAgentSpec(),
        enable_permissions=False,
        mcp_configs=[],
    )
    rail = next(item for item in spec.rails if item.type == registry.RESEARCH_EVIDENCE)
    assert rail.params["enabled"] is True
    assert rail.params["token_budget"] == 1536
    assert rail.params["required_kinds"] == ["literature", "experiment"]
