# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for JiuClawSkillEvolutionRail bootstrap exclusions."""

# pylint: disable=protected-access

import asyncio
from unittest.mock import MagicMock

import pytest
from openjiuwen.harness.rails import SkillEvolutionRail

from jiuwenclaw.agentserver.deep_agent.skill_evolution_rail import JiuClawSkillEvolutionRail


@pytest.fixture
def evolution_rail(monkeypatch: pytest.MonkeyPatch) -> JiuClawSkillEvolutionRail:
    """Construct rail with parent init stubbed; attach store required by super()."""
    monkeypatch.setattr(
        SkillEvolutionRail,
        "__init__",
        lambda self, *args, **kwargs: None,
    )
    rail = JiuClawSkillEvolutionRail(
        skills_dir="/tmp/skills",
        llm=MagicMock(),
        model="test-model",
    )
    rail._evolution_store = MagicMock()
    rail._evolution_store.resolve_skill_dir.return_value = None
    return rail


@pytest.mark.unit
def test_eligible_skill_names_excludes_bootstrap_builtin(evolution_rail, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.skill_evolution_rail.is_bootstrap_builtin_skill",
        lambda name: name == "xlsx",
    )
    assert evolution_rail._eligible_skill_names(["xlsx", "custom-skill"]) == ["custom-skill"]


@pytest.mark.unit
def test_generate_and_emit_experience_skips_bootstrap_builtin(evolution_rail, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.skill_evolution_rail.is_bootstrap_builtin_skill",
        lambda name: name == "xlsx",
    )

    async def _unexpected(*_args, **_kwargs):
        raise AssertionError("super().generate_and_emit_experience should not run for builtin skills")

    monkeypatch.setattr(
        SkillEvolutionRail,
        "generate_and_emit_experience",
        _unexpected,
    )

    result = asyncio.run(evolution_rail.generate_and_emit_experience("xlsx", [], []))
    assert result is False
