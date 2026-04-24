# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""System test: team members inherit parent subagents; tool-activity events wired.

Commit 155a107 added:
- ``assign_team_member_subagents`` copies main DeepAgent's subagent specs onto
  each freshly-spawned team member so members can also call ``dispatch_agent`` /
  ``task_tool`` etc. without rebuilding the list.
- ``team.member.tool_call`` / ``team.member.tool_result`` event types under
  the ``team.member.activity`` category so member tool invocations can be
  broadcast to the UI.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jiuwenclaw.agentserver.team.event_types import (
    EVENT_TYPE_TO_CATEGORY,
    TeamEventCategory,
    TeamEventType,
    get_event_category,
)
from jiuwenclaw.agentserver.team.member_subagents import assign_team_member_subagents

pytestmark = [pytest.mark.integration, pytest.mark.system]


def _fake_agent_with_subagents(subagents):
    agent = MagicMock()
    agent.deep_config = MagicMock()
    agent.deep_config.subagents = subagents
    agent.deep_config.model = MagicMock() if subagents is None else None
    agent.deep_config.workspace = None
    agent.deep_config.language = "cn"
    return agent


def test_member_inherits_subagents_from_main_when_main_has_them():
    main_sub_spec = MagicMock(name="dispatch_agent_spec")
    main = _fake_agent_with_subagents([main_sub_spec])
    member = _fake_agent_with_subagents(None)

    assign_team_member_subagents(main, member)

    assert member.deep_config.subagents == [main_sub_spec]
    # member got its own copy — mutating member's list shouldn't bleed into main
    assert member.deep_config.subagents is not main.deep_config.subagents


def test_member_with_no_deep_config_is_noop():
    main = _fake_agent_with_subagents([MagicMock()])
    member = MagicMock()
    member.deep_config = None

    assign_team_member_subagents(main, member)  # must not raise


def test_member_activity_event_types_are_registered():
    assert TeamEventType.MEMBER_TOOL_CALL.value == "team.member.tool_call"
    assert TeamEventType.MEMBER_TOOL_RESULT.value == "team.member.tool_result"

    assert (
        EVENT_TYPE_TO_CATEGORY[TeamEventType.MEMBER_TOOL_CALL]
        == TeamEventCategory.MEMBER_ACTIVITY
    )
    assert (
        get_event_category(TeamEventType.MEMBER_TOOL_RESULT)
        == TeamEventCategory.MEMBER_ACTIVITY
    )
    assert TeamEventCategory.MEMBER_ACTIVITY.value == "team.member.activity"
