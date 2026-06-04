# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Code-mode TaskPlanningRail — CC-aligned todo tools, no system prompt injection."""

from __future__ import annotations

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail
from openjiuwen.harness.tools.todo import TodoTool
from openjiuwen.harness.workspace.workspace import WorkspaceNode

from jiuwenswarm.agents.harness.code.tools.code_todo_tools import (
    CodeTodoCreateTool,
    CodeTodoGetTool,
    CodeTodoListTool,
    CodeTodoModifyTool,
)


class CodeTaskPlanningRail(TaskPlanningRail):
    """Register code-mode todo tools with CC-aligned metadata.

    - Uses CodeTodo* tool classes (shorter descriptions, coarse-milestone guidance).
    - Skips before_model_call prompt injection (no openjiuwen todo system section).
    """

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        _ = ctx

    def init(self, agent) -> None:
        """Register CC-aligned todo tools on the agent."""
        from openjiuwen.harness.deep_agent import DeepAgent

        if not (
            isinstance(agent, DeepAgent)
            and agent.deep_config
            and hasattr(agent, "ability_manager")
        ):
            return

        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

        if not self.sys_operation:
            self.set_sys_operation(agent.deep_config.sys_operation)
        if not self.workspace:
            self.set_workspace(agent.deep_config.workspace)

        workspace_dir = str(self.workspace.get_node_path(WorkspaceNode.TODO))
        agent_id = getattr(getattr(agent, "card", None), "id", None)

        tool_configs: list[tuple[type, bool]] = [
            (CodeTodoCreateTool, False),
            (CodeTodoListTool, False),
            (CodeTodoGetTool, False),
            (CodeTodoModifyTool, False),
        ]

        existing_tools: list[TodoTool] = []
        for ability in agent.ability_manager.list():
            if isinstance(ability, ToolCard):
                tool_instance = Runner.resource_mgr.get_tool(tool_id=ability.id)
                if tool_instance:
                    for index, (tool_class, found) in enumerate(tool_configs):
                        if isinstance(tool_instance, tool_class):
                            tool_configs[index] = (tool_class, True)
                            existing_tools.append(tool_instance)
                            break

        tools = list(existing_tools)
        try:
            for tool_class, found in tool_configs:
                if not found:
                    new_tool = tool_class(
                        self.sys_operation,
                        workspace_dir,
                        "en",
                        agent_id,
                    )
                    Runner.resource_mgr.add_tool(new_tool)
                    agent.ability_manager.add(new_tool.card)
                    tools.append(new_tool)
            self.tools = tools
        except Exception as exc:
            logger.warning(
                "CodeTaskPlanningRail: failed to add tool, error: %s", exc
            )
