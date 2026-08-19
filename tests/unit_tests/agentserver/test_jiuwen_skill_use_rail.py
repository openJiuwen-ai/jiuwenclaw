# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for JiuWenSkillUseRail per-session tool isolation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard

_RAIL_PATH = (
    Path(__file__).resolve().parents[3]
    / "jiuwenclaw"
    / "agentserver"
    / "deep_agent"
    / "rails"
    / "jiuwen_skill_use_rail.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "jiuwen_skill_use_rail_under_test",
    _RAIL_PATH,
)
assert _SPEC and _SPEC.loader
_RAIL_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_RAIL_MOD)
JiuWenSkillUseRail = _RAIL_MOD.JiuWenSkillUseRail


class _FakeAbilityManager:
    def __init__(self) -> None:
        self.cards: dict[str, ToolCard] = {}

    def get(self, name: str):
        return self.cards.get(name)

    def add(self, card: ToolCard):
        self.cards[card.name] = card
        return SimpleNamespace(added=True)

    def remove(self, name: str):
        self.cards.pop(name, None)

    def registered_tool_ids(self) -> list[tuple[str, str]]:
        """Return (tool_name, tool_id) pairs registered in ability_manager."""
        return [(name, str(card.id)) for name, card in self.cards.items()]


class _FakeResourceManager:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.removed: list[str] = []

    def get_tool(self, tool_id: str):
        return self.tools.get(tool_id)

    def add_tool(self, tool, refresh: bool = False):
        _ = refresh
        card = getattr(tool, "card", None)
        if card is not None:
            self.tools[card.id] = tool

    def remove_tool(self, tool_id: str):
        self.removed.append(tool_id)
        return self.tools.pop(tool_id, None)


def _make_skill_tool(tool_id: str, skills: list[str]):
    card = ToolCard(
        id=tool_id,
        name=tool_id,
        description=f"{tool_id} desc",
        input_params={"type": "object"},
    )

    async def _invoke(inputs, **kwargs):
        _ = inputs, kwargs
        return {"skills": list(skills)}

    def _get_skills():
        return skills

    tool = LocalFunction(card=card, func=_invoke)
    tool.get_skills = _get_skills
    return tool


def _make_agent(agent_id: str):
    return SimpleNamespace(
        card=SimpleNamespace(id=agent_id, name=agent_id),
        ability_manager=_FakeAbilityManager(),
        system_prompt_builder=SimpleNamespace(language="cn"),
        deep_config=SimpleNamespace(enable_read_image_multimodal=True),
    )


def _register_base_tool(rail_module, agent, tool):
    rail_module.tools[tool.card.id] = tool
    agent.ability_manager.add(tool.card)


def _mark_owned_tools(rail, agent) -> None:
    """Mirror SkillUseRail.init ownership tracking for mocked super().init()."""
    owned_ids = getattr(rail, "_owned_tool_ids")
    owned_names = getattr(rail, "_owned_tool_names")
    for name, tool_id in agent.ability_manager.registered_tool_ids():
        owned_ids.add(tool_id)
        owned_names.add(name)


def _noop_super_init(self, agent):
    _ = self, agent


@pytest.fixture
def rail_module():
    resource_mgr = _FakeResourceManager()
    fake_runner = SimpleNamespace(resource_mgr=resource_mgr)
    _RAIL_MOD.Runner = fake_runner
    import jiuwenclaw.agentserver.deep_agent.tool_qualify as tool_qualify_mod

    tool_qualify_mod.Runner = fake_runner
    return resource_mgr


def test_two_agents_get_distinct_qualified_skill_tools(rail_module, monkeypatch):
    """Two agents should register non-overlapping qualified skill_tool ids."""
    monkeypatch.setattr(JiuWenSkillUseRail.__bases__[0], "init", _noop_super_init)

    rail_a = JiuWenSkillUseRail(skills_dir=[], skill_mode=JiuWenSkillUseRail.SKILL_MODE_ALL)
    rail_b = JiuWenSkillUseRail(skills_dir=[], skill_mode=JiuWenSkillUseRail.SKILL_MODE_ALL)
    agent_a = _make_agent("jiuwenclaw_session_a")
    agent_b = _make_agent("jiuwenclaw_session_b")

    tool_a = _make_skill_tool("skill_tool", ["skill-a"])
    tool_b = _make_skill_tool("skill_tool", ["skill-b"])
    _register_base_tool(rail_module, agent_a, tool_a)
    _mark_owned_tools(rail_a, agent_a)
    rail_a.init(agent_a)

    _register_base_tool(rail_module, agent_b, tool_b)
    _mark_owned_tools(rail_b, agent_b)
    rail_b.init(agent_b)

    id_a = "skill_tool_jiuwenclaw_session_a"
    id_b = "skill_tool_jiuwenclaw_session_b"
    assert id_a in rail_module.tools
    assert id_b in rail_module.tools
    assert rail_module.tools[id_a].get_skills() == ["skill-a"]
    assert rail_module.tools[id_b].get_skills() == ["skill-b"]


def test_uninit_removes_only_own_qualified_tools(rail_module, monkeypatch):
    monkeypatch.setattr(JiuWenSkillUseRail.__bases__[0], "init", _noop_super_init)

    rail = JiuWenSkillUseRail(skills_dir=[], skill_mode=JiuWenSkillUseRail.SKILL_MODE_ALL)
    agent = _make_agent("jiuwenclaw_session_x")
    tool = _make_skill_tool("skill_tool", ["only-x"])
    _register_base_tool(rail_module, agent, tool)
    _mark_owned_tools(rail, agent)

    rail.init(agent)
    qualified_id = "skill_tool_jiuwenclaw_session_x"
    assert qualified_id in rail_module.tools

    other_tool = _make_skill_tool("skill_tool_jiuwenclaw_session_y", ["keep-y"])
    rail_module.tools["skill_tool_jiuwenclaw_session_y"] = other_tool

    rail.uninit(agent)

    assert qualified_id not in rail_module.tools
    assert "skill_tool_jiuwenclaw_session_y" in rail_module.tools
    assert agent.ability_manager.get("skill_tool") is None


def test_init_calls_super_and_qualifies_owned_tools(monkeypatch, rail_module, tmp_path):
    """Integration-style: super().init registers base id, subclass re-qualifies."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "demo").mkdir()
    (skills_dir / "demo" / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")

    def _fake_super_init(self, agent):
        tool = _make_skill_tool("skill_tool", ["demo"])
        _register_base_tool(rail_module, agent, tool)
        getattr(self, "_owned_tool_ids").add("skill_tool")
        getattr(self, "_owned_tool_names").add("skill_tool")

    monkeypatch.setattr(
        JiuWenSkillUseRail.__bases__[0],
        "init",
        _fake_super_init,
    )

    rail = JiuWenSkillUseRail(skills_dir=[str(skills_dir)], skill_mode=JiuWenSkillUseRail.SKILL_MODE_ALL)
    agent = _make_agent("jiuwenclaw_demo")

    rail.init(agent)

    assert "skill_tool_jiuwenclaw_demo" in rail_module.tools
    card = agent.ability_manager.get("skill_tool")
    assert card is not None
    assert card.id == "skill_tool_jiuwenclaw_demo"


def test_init_qualifies_all_tools_tracked_by_super_init(rail_module, monkeypatch):
    """Every tool id in _owned_tool_ids after super().init() gets session-qualified."""
    monkeypatch.setattr(JiuWenSkillUseRail.__bases__[0], "init", _noop_super_init)

    rail = JiuWenSkillUseRail(skills_dir=[], skill_mode=JiuWenSkillUseRail.SKILL_MODE_ALL)
    agent = _make_agent("jiuwenclaw_multi")

    for tool_id in ("skill_tool", "skill_complete", "list_skill"):
        _register_base_tool(rail_module, agent, _make_skill_tool(tool_id, []))
    _mark_owned_tools(rail, agent)

    rail.init(agent)

    for tool_id in ("skill_tool", "skill_complete", "list_skill"):
        qualified_id = f"{tool_id}_jiuwenclaw_multi"
        assert qualified_id in rail_module.tools
        card = agent.ability_manager.get(tool_id)
        assert card is not None
        assert card.id == qualified_id
