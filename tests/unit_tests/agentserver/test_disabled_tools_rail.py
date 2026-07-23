from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from jiuwenclaw.agentserver.deep_agent.rails.disabled_tools_rail import DisabledToolsRail


def test_disabled_tools_rail_default_is_ability_only() -> None:
    rail = DisabledToolsRail(disabled_tools=["bash"])
    assert rail._touch_shared_resource_mgr is False


def test_unregister_does_not_remove_from_shared_resource_mgr(monkeypatch) -> None:
    tool_card = SimpleNamespace(id="bash", name="bash")
    tool = object()

    ability_manager = MagicMock()
    ability_manager.get.return_value = tool_card

    agent = SimpleNamespace(ability_manager=ability_manager)

    resource_mgr = MagicMock()
    resource_mgr.get_tool.return_value = tool
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.rails.disabled_tools_rail.Runner.resource_mgr",
        resource_mgr,
    )

    rail = DisabledToolsRail(disabled_tools=["bash"], touch_shared_resource_mgr=False)
    rail.init(agent)

    ability_manager.remove.assert_called_once_with("bash")
    resource_mgr.get_tool.assert_called_once_with("bash")
    resource_mgr.remove_tool.assert_not_called()
    assert "bash" in rail._removed_data


def test_unregister_can_opt_in_to_shared_resource_mgr(monkeypatch) -> None:
    tool_card = SimpleNamespace(id="bash", name="bash")
    tool = object()

    ability_manager = MagicMock()
    ability_manager.get.return_value = tool_card
    agent = SimpleNamespace(ability_manager=ability_manager)

    resource_mgr = MagicMock()
    resource_mgr.get_tool.return_value = tool
    resource_mgr.remove_tool.return_value = MagicMock(is_ok=lambda: True)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.rails.disabled_tools_rail.Runner.resource_mgr",
        resource_mgr,
    )

    rail = DisabledToolsRail(disabled_tools=["bash"], touch_shared_resource_mgr=True)
    rail.init(agent)

    resource_mgr.remove_tool.assert_called_once_with("bash")
