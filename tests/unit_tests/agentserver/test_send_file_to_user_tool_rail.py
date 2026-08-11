# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task 3: SendFileToUserToolRail registers send_file_to_user on the team leader.

Team mode bypasses _update_session_tools (interface_deep.py:8885-8919 returns
before :9023), so the leader never received send_file_to_user. This rail
mirrors _update_session_tools:5366-5381 + AskUserQuestionToolRail to register
it on the leader's ability_manager at rail init.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jiuwenclaw.agentserver.team.rails.send_file_to_user_tool_rail import (
    SendFileToUserToolRail,
)


def _fake_agent():
    agent = MagicMock()
    agent.ability_manager = MagicMock()
    agent.ability_manager.get.return_value = None
    agent.ability_manager.list.return_value = []
    return agent


def _stub_toolkit(monkeypatch, tools):
    toolkit = MagicMock()
    toolkit.get_tools.return_value = list(tools)
    import jiuwenclaw.agentserver.team.rails.send_file_to_user_tool_rail as rail_mod

    monkeypatch.setattr(rail_mod, "SendFileToolkit", lambda **kw: toolkit)
    runner = MagicMock()
    runner.resource_mgr.get_tool.return_value = None
    monkeypatch.setattr(rail_mod, "Runner", runner)
    return runner


def _tool(name="send_file_to_user"):
    tool = MagicMock()
    tool.card.id = name
    tool.card.name = name
    return tool


def test_init_registers_send_file_to_user_on_leader_when_gate_met(monkeypatch):
    rail = SendFileToUserToolRail(
        request_id="req-1",
        session_id="officeclaw_s1",
        channel="officeclaw",
        config={"channels": {"officeclaw": {"send_file_allowed": True}}},
    )
    tool = _tool()
    runner = _stub_toolkit(monkeypatch, [tool])
    agent = _fake_agent()
    rail.init(agent)
    assert rail._registered is True
    runner.resource_mgr.add_tool.assert_called_once_with(tool)
    agent.ability_manager.add.assert_called_once_with(tool.card)


def test_init_skips_when_channel_not_allowed_and_not_officeclaw():
    rail = SendFileToUserToolRail(
        request_id="req-1",
        session_id="officeclaw_s1",
        channel="web",
        config={"channels": {"web": {"send_file_allowed": False}}},
    )
    agent = _fake_agent()
    rail.init(agent)
    assert rail._registered is False
    agent.ability_manager.add.assert_not_called()


def test_init_skips_when_request_context_missing():
    rail = SendFileToUserToolRail(
        request_id=None,
        session_id="officeclaw_s1",
        channel="officeclaw",
        config={"channels": {"officeclaw": {"send_file_allowed": True}}},
    )
    agent = _fake_agent()
    rail.init(agent)
    assert rail._registered is False
    agent.ability_manager.add.assert_not_called()


def test_init_skips_when_session_id_empty():
    rail = SendFileToUserToolRail(
        request_id="req-1",
        session_id="",
        channel="officeclaw",
        config={"channels": {"officeclaw": {"send_file_allowed": True}}},
    )
    agent = _fake_agent()
    rail.init(agent)
    assert rail._registered is False
    agent.ability_manager.add.assert_not_called()


def test_init_officeclaw_channel_registers_even_if_config_flag_false(monkeypatch):
    # interface_deep.py:5366: send_file_channel_allowed = send_file_enabled or channel == "officeclaw"
    rail = SendFileToUserToolRail(
        request_id="req-1",
        session_id="officeclaw_s1",
        channel="officeclaw",
        config={"channels": {}},  # no flag, but officeclaw bypass
    )
    tool = _tool()
    runner = _stub_toolkit(monkeypatch, [tool])
    agent = _fake_agent()
    rail.init(agent)
    assert rail._registered is True
    runner.resource_mgr.add_tool.assert_called_once_with(tool)


def test_init_is_idempotent(monkeypatch):
    rail = SendFileToUserToolRail(
        request_id="req-1",
        session_id="officeclaw_s1",
        channel="officeclaw",
        config={"channels": {"officeclaw": {"send_file_allowed": True}}},
    )
    tool = _tool()
    runner = _stub_toolkit(monkeypatch, [tool])
    agent = _fake_agent()
    rail.init(agent)
    rail.init(agent)  # second call no-op
    assert runner.resource_mgr.add_tool.call_count == 1


def test_init_failure_keeps_not_registered(monkeypatch):
    rail = SendFileToUserToolRail(
        request_id="req-1",
        session_id="officeclaw_s1",
        channel="officeclaw",
        config={"channels": {"officeclaw": {"send_file_allowed": True}}},
    )

    def boom(**kw):
        raise RuntimeError("boom")

    import jiuwenclaw.agentserver.team.rails.send_file_to_user_tool_rail as rail_mod

    monkeypatch.setattr(rail_mod, "SendFileToolkit", boom)
    runner = MagicMock()
    runner.resource_mgr.get_tool.return_value = None
    monkeypatch.setattr(rail_mod, "Runner", runner)
    agent = _fake_agent()
    rail.init(agent)  # must not raise
    assert rail._registered is False
    agent.ability_manager.add.assert_not_called()
