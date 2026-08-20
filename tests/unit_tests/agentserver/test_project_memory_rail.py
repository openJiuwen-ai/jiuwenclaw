"""Focused tests for the migrated ProjectMemoryRail."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jiuwenclaw.agentserver.deep_agent.rails.project_memory import (
    SECTION_NAME,
    clear_project_memory_cache,
)
from jiuwenclaw.agentserver.deep_agent.rails.project_memory_rail import (
    ProjectMemoryRail,
)


def _agent() -> MagicMock:
    builder = MagicMock()
    builder.added_sections = []

    def add_section(section):
        builder.added_sections = [
            item for item in builder.added_sections if item.name != section.name
        ]
        builder.added_sections.append(section)

    def remove_section(name):
        builder.added_sections = [
            item for item in builder.added_sections if item.name != name
        ]

    builder.add_section.side_effect = add_section
    builder.remove_section.side_effect = remove_section
    agent = MagicMock()
    agent.system_prompt_builder = builder
    return agent


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        inputs=SimpleNamespace(tool_name="read_file"),
        extra={},
        session=SimpleNamespace(session_id="test-session"),
    )


@pytest.mark.asyncio
async def test_project_memory_is_injected_and_refreshes(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    memory_file = tmp_path / "JIUWENSWARM.md"
    memory_file.write_text("RULE-V1", encoding="utf-8")
    clear_project_memory_cache()

    agent = _agent()
    rail = ProjectMemoryRail(workspace=str(tmp_path), language="en")
    rail.init(agent)
    await rail.before_model_call(_ctx())

    section = next(
        item
        for item in agent.system_prompt_builder.added_sections
        if item.name == SECTION_NAME
    )
    assert "RULE-V1" in section.render("en")

    memory_file.write_text("RULE-V2", encoding="utf-8")
    await rail.before_model_call(_ctx())
    section = next(
        item
        for item in agent.system_prompt_builder.added_sections
        if item.name == SECTION_NAME
    )
    assert "RULE-V2" in section.render("en")
    assert "RULE-V1" not in section.render("en")
    clear_project_memory_cache()


@pytest.mark.asyncio
async def test_empty_project_memory_does_not_add_section(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    clear_project_memory_cache()

    agent = _agent()
    rail = ProjectMemoryRail(workspace=str(tmp_path))
    rail.init(agent)
    await rail.before_model_call(_ctx())

    assert not any(
        item.name == SECTION_NAME for item in agent.system_prompt_builder.added_sections
    )
    clear_project_memory_cache()
