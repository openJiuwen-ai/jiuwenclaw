# coding: utf-8
"""Regression tests for JiuClawQAArtifactRail load_qa_index registration.

load_qa_index must be registered in Runner.resource_mgr (not only ability_manager),
otherwise AbilityManager raises Tool instance not found on invoke.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from openjiuwen.core.context_engine.qa_artifact import LOAD_QA_INDEX_TOOL_NAME, LoadQaIndexTool

from jiuwenclaw.agentserver.deep_agent.rails.qa_artifact_rail import JiuClawQAArtifactRail
from jiuwenclaw.agentserver.deep_agent.tool_qualify import qualify_tool_id


class _FakeOkResult:
    @staticmethod
    def is_err() -> bool:
        return False


class _FakeResourceMgr:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def add_tool(self, tool):
        card = getattr(tool, "card", None)
        if card is not None:
            self.tools[card.id] = tool
        return _FakeOkResult()

    def get_tool(self, tool_id, tag=None, session=None):
        return self.tools.get(tool_id)

    def remove_tool(self, tool_id):
        self.tools.pop(tool_id, None)


@pytest.fixture
def resource_mgr(monkeypatch):
    mgr = _FakeResourceMgr()
    import jiuwenclaw.agentserver.deep_agent.tool_qualify as tool_qualify_mod

    monkeypatch.setattr(tool_qualify_mod.Runner, "resource_mgr", mgr)
    return mgr


def _make_agent(agent_card_id: str = "jiuwenclaw_officeclaw_test_session"):
    ability_manager = MagicMock()
    ability_manager.get.return_value = None
    card = MagicMock()
    card.id = agent_card_id
    agent = MagicMock()
    agent.card = card
    agent.ability_manager = ability_manager
    agent.react_agent = None
    return agent


def test_init_registers_load_qa_index_in_resource_mgr(resource_mgr):
    agent = _make_agent()
    rail = JiuClawQAArtifactRail()

    rail.init(agent)

    tool_id = qualify_tool_id(LoadQaIndexTool.TOOL_ID, agent.card.id)
    registered_tool = resource_mgr.get_tool(tool_id)
    assert registered_tool is not None
    assert registered_tool.card.name == LOAD_QA_INDEX_TOOL_NAME
    assert registered_tool.card.id == tool_id
    agent.ability_manager.add.assert_called()
    added_card = agent.ability_manager.add.call_args.args[0]
    assert added_card.id == tool_id
    assert added_card.name == LOAD_QA_INDEX_TOOL_NAME


def test_uninit_removes_load_qa_index_from_resource_mgr(resource_mgr):
    agent = _make_agent()
    rail = JiuClawQAArtifactRail()
    rail.init(agent)
    tool_id = qualify_tool_id(LoadQaIndexTool.TOOL_ID, agent.card.id)

    rail.uninit(agent)

    assert resource_mgr.get_tool(tool_id) is None
    agent.ability_manager.remove.assert_called_once_with(LOAD_QA_INDEX_TOOL_NAME)


def test_init_skips_when_agent_card_id_missing(resource_mgr):
    agent = _make_agent()
    agent.card.id = None
    rail = JiuClawQAArtifactRail()

    rail.init(agent)

    assert resource_mgr.tools == {}
    agent.ability_manager.add.assert_not_called()


def test_uninit_safe_when_init_skipped(resource_mgr):
    agent = _make_agent()
    agent.card.id = None
    rail = JiuClawQAArtifactRail()

    rail.init(agent)
    rail.uninit(agent)

    assert resource_mgr.tools == {}


def test_uninit_clears_state_when_ability_manager_remove_raises(resource_mgr):
    agent = _make_agent()
    agent.ability_manager.remove.side_effect = RuntimeError("remove failed")
    rail = JiuClawQAArtifactRail()
    rail.init(agent)
    tool_id = qualify_tool_id(LoadQaIndexTool.TOOL_ID, agent.card.id)

    with pytest.raises(RuntimeError, match="remove failed"):
        rail.uninit(agent)

    assert resource_mgr.get_tool(tool_id) is None
    agent.ability_manager.remove.side_effect = None

    rail.init(agent)
    assert resource_mgr.get_tool(tool_id) is not None
