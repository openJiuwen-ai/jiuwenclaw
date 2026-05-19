# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Test subagents for SkillDev Agent — eval generation, execution, grading."""

from __future__ import annotations

from typing import Any, List, Optional

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.schema.config import SubAgentConfig

from jiuwenclaw.agentserver.skilldev_agent.subagents.test_case_generator import (
    build_test_case_generator_config,
)
from jiuwenclaw.agentserver.skilldev_agent.subagents.skill_executor import (
    build_skill_executor_config,
)
from jiuwenclaw.agentserver.skilldev_agent.subagents.grader import (
    build_grader_config,
)


def build_skilldev_subagents(
    model: Model,
    *,
    language: str = "cn",
    sys_operation: Optional[SysOperation] = None,
    agent_id: Optional[str] = None,
) -> list[SubAgentConfig]:
    """Build all test-related subagent configs for SkillDev Agent.

    Returns a list of SubAgentConfig that will be passed to create_deep_agent(subagents=...).
    The framework automatically registers SubagentRail + task_tool.

    Note: workspace is intentionally NOT set in SubAgentConfig. This allows
    create_subagent to dynamically derive the subagent's workspace from the
    parent's current workspace at invocation time, avoiding stale workspace
    references across sessions. Subagents access parent files via absolute
    paths passed in task_description.
    """
    return [
        # build_test_case_generator_config(
        #     model, language=language,
        #     sys_operation=sys_operation,
        # ),
        build_skill_executor_config(
            model,
            language=language,
            sys_operation=sys_operation,
            agent_id=agent_id,
        ),
        build_grader_config(
            model, language=language,
            sys_operation=sys_operation,
        ),
    ]


__all__ = [
    "build_skilldev_subagents",
    "build_test_case_generator_config",
    "build_skill_executor_config",
    "build_grader_config",
]
