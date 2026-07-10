# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Concurrent-safe rail subclasses that pre-check resource_mgr before add_tool.

When multiple concurrent agents share the same bot_id/service_id (e.g. load test
with ``--shards 1``), their rails register the same tools (same resource_id).
Only the first agent's ``add_tool`` succeeds; the rest get "resource already
exist" ERROR logs (~1560 lines per 60-concurrent run).

These subclasses override ``init()`` to check ``Runner.resource_mgr.get_tool()``
*before* calling ``add_tool``, skipping the registration when the tool already
exists.  Tool cards are still added to the agent's own ``ability_manager`` so
the agent can use them.
"""

from __future__ import annotations

import logging

from openjiuwen.core.runner import Runner
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail
from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail
from openjiuwen.harness.tools import BashTool
from openjiuwen.harness.tools.code import CodeTool
from openjiuwen.harness.tools.filesystem import (
    EditFileTool,
    GlobTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from openjiuwen.harness.tools.todo import TodoCreateTool, TodoListTool, TodoModifyTool
from openjiuwen.harness.workspace.workspace import WorkspaceNode

logger = logging.getLogger("jiuwenclaw.app")


class ConcurrentSafeFileSystemRail(FileSystemRail):
    """FileSystemRail subclass that skips duplicate tool registration."""

    def init(self, agent) -> None:
        lang = getattr(agent.system_prompt_builder, "language", None) or "cn"
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        workspace_path = str(self.workspace.root_path) if self.workspace else None
        enable_read_image_multimodal = self._enable_read_image_multimodal
        if enable_read_image_multimodal is None:
            deep_config = getattr(agent, "deep_config", None)
            enable_read_image_multimodal = bool(
                getattr(deep_config, "enable_read_image_multimodal", True)
            )
        read_tool = ReadFileTool(
            self.sys_operation,
            lang,
            agent_id,
            enable_image_multimodal=enable_read_image_multimodal,
        )
        write_tool = WriteFileTool(self.sys_operation, lang, agent_id, workspace_path=workspace_path)
        edit_tool = EditFileTool(self.sys_operation, lang, agent_id, workspace_path=workspace_path)
        glob_tool = GlobTool(self.sys_operation, lang, agent_id)
        list_dir_tool = ListDirTool(self.sys_operation, lang, agent_id)
        grep_tool = GrepTool(self.sys_operation, lang, agent_id)
        bash_tool = BashTool(self.sys_operation, lang, agent_id=agent_id)
        code_tool = CodeTool(self.sys_operation, lang, agent_id)

        self.tools = [
            read_tool,
            write_tool,
            edit_tool,
            glob_tool,
            list_dir_tool,
            grep_tool,
            bash_tool,
            code_tool,
        ]

        new_tools = [
            t for t in self.tools
            if Runner.resource_mgr.get_tool(t.card.id) is None
        ]
        if new_tools:
            Runner.resource_mgr.add_tool(new_tools)

        for tool in self.tools:
            agent.ability_manager.add(tool.card)


class ConcurrentSafeTaskPlanningRail(TaskPlanningRail):
    """TaskPlanningRail subclass that checks resource_mgr before add_tool."""

    def init(self, agent) -> None:
        from openjiuwen.harness.deep_agent import DeepAgent
        from openjiuwen.core.foundation.tool import ToolCard

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
        language = self.system_prompt_builder.language if self.system_prompt_builder else "cn"

        tool_configs = [
            (TodoCreateTool, False),
            (TodoListTool, False),
            (TodoModifyTool, False),
        ]

        existing_tools = []
        for ability in agent.ability_manager.list():
            if isinstance(ability, ToolCard):
                tool_instance = Runner.resource_mgr.get_tool(tool_id=ability.id)
                if tool_instance:
                    for i, (tool_class, found) in enumerate(tool_configs):
                        if isinstance(tool_instance, tool_class):
                            tool_configs[i] = (tool_class, True)
                            existing_tools.append(tool_instance)
                            break

        tools = existing_tools.copy()
        try:
            for tool_class, found in tool_configs:
                if not found:
                    new_tool = tool_class(self.sys_operation, workspace_dir, language, agent_id)
                    if Runner.resource_mgr.get_tool(new_tool.card.id) is not None:
                        agent.ability_manager.add(new_tool.card)
                        tools.append(new_tool)
                        continue
                    Runner.resource_mgr.add_tool(new_tool)
                    agent.ability_manager.add(new_tool.card)
                    tools.append(new_tool)
            self.tools = tools
        except Exception as exc:
            logger.warning("ConcurrentSafeTaskPlanningRail: failed to add tool, error: %s", exc)


__all__ = [
    "ConcurrentSafeFileSystemRail",
    "ConcurrentSafeTaskPlanningRail",
]
