# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""System test: dispatch_agent tool wiring (commit 155a107).

Checks that the new free-form subagent spawn tool is registered with the
expected name / schema / validation behavior, without actually spawning a
subagent (which would require a live model).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jiuwenclaw.agentserver.deep_agent.tools.dispatch_agent_tool import (
    DispatchAgentMetadataProvider,
    DispatchAgentTool,
    create_dispatch_agent_tool,
)

pytestmark = [pytest.mark.integration, pytest.mark.system]


def _fake_parent_agent() -> MagicMock:
    parent = MagicMock()
    parent.deep_config = MagicMock()
    parent.deep_config.subagents = []
    parent.deep_config.model = None
    parent.deep_config.workspace = None
    parent.deep_config.language = "cn"
    parent.deep_config.max_iterations = 8
    return parent


def test_dispatch_agent_tool_is_registered_with_expected_card():
    parent = _fake_parent_agent()
    tools = create_dispatch_agent_tool(parent_agent=parent, language="cn")

    assert len(tools) == 1
    tool = tools[0]
    assert isinstance(tool, DispatchAgentTool)
    assert tool.card.name == "dispatch_agent"


def test_dispatch_agent_metadata_exposes_required_params_cn_and_en():
    provider = DispatchAgentMetadataProvider()

    assert provider.get_name() == "dispatch_agent"

    cn_schema = provider.get_input_params("cn")
    en_schema = provider.get_input_params("en")
    for schema in (cn_schema, en_schema):
        assert schema["type"] == "object"
        assert set(schema["required"]) == {
            "name",
            "description",
            "system_prompt",
            "task_description",
        }

    assert provider.get_description("cn") != provider.get_description("en")


@pytest.mark.asyncio
async def test_dispatch_agent_invoke_rejects_missing_session():
    parent = _fake_parent_agent()
    tool = create_dispatch_agent_tool(parent_agent=parent, language="cn")[0]

    with pytest.raises(Exception):
        await tool.invoke(
            {
                "name": "reviewer",
                "description": "check slides",
                "system_prompt": "you are a reviewer",
                "task_description": "review slides",
            }
        )


@pytest.mark.asyncio
async def test_dispatch_agent_invoke_rejects_missing_required_fields():
    parent = _fake_parent_agent()
    tool = create_dispatch_agent_tool(parent_agent=parent, language="cn")[0]

    fake_session = MagicMock()
    fake_session.get_session_id = MagicMock(return_value="sess-test")

    from openjiuwen.core.session.agent import Session

    fake_session.__class__ = Session  # isinstance check in invoke

    with pytest.raises(Exception):
        await tool.invoke(
            {"name": "reviewer", "description": "", "system_prompt": "", "task_description": ""},
            session=fake_session,
        )
