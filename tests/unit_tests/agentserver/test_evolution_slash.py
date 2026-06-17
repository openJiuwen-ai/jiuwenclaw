# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for rail-independent evolution slash handling."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from jiuwenswarm.server.runtime.agent_adapter.evolution_slash import (
    EvolutionSlashContext,
    handle_evolution_slash_command,
)


def _write_skill(tmp_path, name: str, *, kind: str | None = None) -> str:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True)
    if kind is None:
        content = f"# {name}\n"
    else:
        content = f"---\nname: {name}\nkind: {kind}\n---\n# {name}\n"
    skill_dir.joinpath("SKILL.md").write_text(content, encoding="utf-8")
    return str(skills_dir)


@pytest.mark.anyio
async def test_agent_plan_evolve_uses_actual_swarm_skill_kind(tmp_path):
    skills_dir = _write_skill(tmp_path, "research-team", kind="swarm-skill")

    result = await handle_evolution_slash_command(
        "/evolve research-team improve review flow",
        EvolutionSlashContext(
            mode="agent.plan",
            session_id="sess-agent-plan",
            skills_dir=skills_dir,
            evolution_enabled=True,
        ),
    )

    assert result is not None
    assert result["result_type"] == "followup"
    assert 'subject={"kind": "swarm-skill", "name": "research-team"}' in result["followup_prompt"]


@pytest.mark.anyio
async def test_agent_plan_evolve_defaults_untyped_skill_to_skill_kind(tmp_path):
    skills_dir = _write_skill(tmp_path, "regular-skill")

    result = await handle_evolution_slash_command(
        "/evolve regular-skill improve retry flow",
        EvolutionSlashContext(
            mode="agent.plan",
            session_id="sess-agent-plan",
            skills_dir=skills_dir,
            evolution_enabled=True,
        ),
    )

    assert result is not None
    assert result["result_type"] == "followup"
    assert 'subject={"kind": "skill", "name": "regular-skill"}' in result["followup_prompt"]


@pytest.mark.anyio
async def test_agent_plan_evolve_allows_missing_user_intent(tmp_path):
    skills_dir = _write_skill(tmp_path, "regular-skill")

    result = await handle_evolution_slash_command(
        "/evolve regular-skill",
        EvolutionSlashContext(
            mode="agent.plan",
            session_id="sess-agent-plan",
            skills_dir=skills_dir,
            evolution_enabled=True,
        ),
    )

    assert result is not None
    assert result["result_type"] == "followup"
    assert result["action"] == "run_evolve_followup"
    assert result["skill_name"] == "regular-skill"
    assert 'user_intent=""' in result["followup_prompt"]


@pytest.mark.anyio
async def test_team_evolve_allows_missing_user_intent(tmp_path):
    skills_dir = _write_skill(tmp_path, "regular-skill")

    result = await handle_evolution_slash_command(
        "/evolve regular-skill",
        EvolutionSlashContext(
            mode="team",
            session_id="sess-team",
            skills_dir=skills_dir,
            evolution_enabled=True,
        ),
    )

    assert result is not None
    assert result["result_type"] == "followup"
    assert result["action"] == "run_evolve_followup"
    assert result["skill_name"] == "regular-skill"
    assert 'user_intent=""' in result["followup_prompt"]


@pytest.mark.anyio
async def test_agent_plan_evolve_simplify_no_records_returns_answer(tmp_path, monkeypatch):
    skills_dir = _write_skill(tmp_path, "research-team", kind="swarm-skill")
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.evolution_slash.ExperienceQueryService.list_experiences",
        AsyncMock(return_value={"items": [], "has_more": False}),
    )

    result = await handle_evolution_slash_command(
        "/evolve_simplify research-team",
        EvolutionSlashContext(
            mode="agent.plan",
            session_id="sess-agent-plan",
            skills_dir=skills_dir,
            evolution_enabled=True,
        ),
    )

    assert result is not None
    assert result["result_type"] == "answer"
    assert result["output"] == "Skill 'research-team' 暂无演进经验，无需整理。"


@pytest.mark.anyio
async def test_agent_plan_evolve_rebuild_returns_followup(tmp_path, monkeypatch):
    skills_dir = _write_skill(tmp_path, "research-team", kind="swarm-skill")
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.evolution_slash.ExperienceRebuildService.prepare_rebuild_context",
        AsyncMock(
            return_value={
                "records": [
                    {
                        "record_id": "r1",
                        "summary": "normalize prompt",
                        "target": "body",
                        "section": "Troubleshooting",
                        "score": 0.99,
                        "updated_at": "2026-06-15T00:00:00+00:00",
                        "content": "old content",
                    }
                ],
                "overflow_index": {},
            }
        ),
    )

    result = await handle_evolution_slash_command(
        "/evolve_rebuild research-team optimize",
        EvolutionSlashContext(
            mode="agent.plan",
            session_id="sess-agent-plan",
            skills_dir=skills_dir,
            evolution_enabled=True,
        ),
    )

    assert result is not None
    assert result["result_type"] == "followup"
    assert result["action"] == "run_rebuild_followup"
    assert result["skill_name"] == "research-team"
