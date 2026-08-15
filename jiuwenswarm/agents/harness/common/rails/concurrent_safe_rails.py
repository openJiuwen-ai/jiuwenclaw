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
from openjiuwen.harness.rails import SysOperationRail
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail
from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail
from openjiuwen.harness.schema.task import TaskItem, TaskPlan, TaskStatus
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
from openjiuwen.harness.tools.todo import (
    TodoCreateTool,
    TodoItem,
    TodoListTool,
    TodoModifyTool,
    TodoStatus,
)
from openjiuwen.harness.workspace.workspace import WorkspaceNode

logger = logging.getLogger(__name__)

_TODO_WRITE_TOOLS = frozenset({"todo_create", "todo_modify"})


class ConcurrentSafeSysOperationRail(SysOperationRail):
    """SysOperationRail subclass that skips duplicate tool registration."""

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


# Alias for FileSystemRail naming in configs / legacy docs.
ConcurrentSafeFileSystemRail = ConcurrentSafeSysOperationRail


class ConcurrentSafeTaskPlanningRail(TaskPlanningRail):
    """TaskPlanningRail subclass that checks resource_mgr before add_tool.

    Hardens todo↔TaskPlan consistency around permission interrupts:
    - refresh TaskPlan from disk after todo writes (and before sync)
    - clear TaskPlan on interrupt so stale bridge cannot drive OuterLoop
    - never let ``_sync_todos_from_plan`` downgrade a completed todo
    """

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

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        await super().after_tool_call(ctx)
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        tool_name = ctx.inputs.tool_name or ""
        if tool_name not in _TODO_WRITE_TOOLS:
            return
        # Always re-read disk: interrupt leaves file unchanged; successful
        # writes update file first. Do not gate on tool_result — resume paths
        # may fire after_tool_call with an empty result even after a real write.
        await self._refresh_task_plan_from_todos(ctx)

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        """Keep TaskPlan aligned with disk; clear it on interrupt."""
        if self._iteration_interrupted(ctx):
            await self._clear_task_plan(ctx)
            logger.info(
                "ConcurrentSafeTaskPlanningRail: cleared TaskPlan after interrupt "
                "(skip stale bridge/sync)"
            )
            return

        await self._refresh_task_plan_from_todos(ctx)
        await super().after_task_iteration(ctx)

    async def _sync_todos_from_plan(self, ctx: AgentCallbackContext) -> None:
        """Sync TaskPlan → todo.json, but never downgrade completed todos."""
        from datetime import datetime, timezone

        if ctx.session is None:
            return

        state = ctx.agent.load_state(ctx.session)  # type: ignore[attr-defined]
        plan = state.task_plan
        if plan is None or len(plan.tasks) == 0:
            return

        tool = self._find_todo_tool()
        if tool is None:
            return

        session_id = ctx.session.get_session_id()
        file_path = tool.file_path_for_session(session_id)

        try:
            todos = await tool.load_todos(file_path)
        except Exception:
            logger.debug("ConcurrentSafeTaskPlanningRail: no todos for sync")
            return

        if not todos:
            return

        status_by_task_id = {
            task.id: self._to_todo_status(task.status)
            for task in plan.tasks
        }
        changed = False
        skipped_downgrade = 0
        now = datetime.now(timezone.utc).isoformat()

        for todo in todos:
            desired = status_by_task_id.get(todo.id)
            if desired is None or todo.status == desired:
                continue
            if (
                todo.status == TodoStatus.COMPLETED
                and desired in (TodoStatus.PENDING, TodoStatus.IN_PROGRESS)
            ):
                skipped_downgrade += 1
                continue
            todo.status = desired
            todo.updatedAt = now
            changed = True

        if skipped_downgrade:
            logger.info(
                "ConcurrentSafeTaskPlanningRail: skipped %d completed→active downgrade(s)",
                skipped_downgrade,
            )

        if not changed:
            return

        await tool.save_todos(todos, file_path)
        logger.info(
            "ConcurrentSafeTaskPlanningRail: synced %d todos from TaskPlan",
            len(todos),
        )

    async def _refresh_task_plan_from_todos(self, ctx: AgentCallbackContext) -> None:
        """Overwrite TaskPlan from persisted todos (TodoItem → TaskItem)."""
        if ctx.session is None:
            return

        tool = self._find_todo_tool()
        if tool is None:
            return

        agent = ctx.agent
        if agent is None or not hasattr(agent, "load_state") or not hasattr(agent, "save_state"):
            return

        session_id = ctx.session.get_session_id()
        file_path = tool.file_path_for_session(session_id)
        try:
            todos = await tool.load_todos(file_path)
        except Exception:
            logger.debug(
                "ConcurrentSafeTaskPlanningRail: refresh TaskPlan skipped, load_todos failed"
            )
            return

        if not todos:
            return

        plan_tasks = [self._todo_to_task_item(todo) for todo in todos]
        in_progress = next(
            (task for task in plan_tasks if task.status == TaskStatus.IN_PROGRESS),
            None,
        )

        state = agent.load_state(ctx.session)
        if state.task_plan is None:
            state.task_plan = TaskPlan(
                goal="refreshed from todo list",
                tasks=plan_tasks,
                current_task_id=in_progress.id if in_progress else None,
            )
        else:
            state.task_plan.tasks = plan_tasks
            state.task_plan.current_task_id = in_progress.id if in_progress else None

        agent.save_state(ctx.session, state)

        logger.info(
            "ConcurrentSafeTaskPlanningRail: refreshed TaskPlan from todos (%s)",
            state.task_plan.get_progress_summary(),
        )

    async def _clear_task_plan(self, ctx: AgentCallbackContext) -> None:
        if ctx.session is None:
            return
        agent = ctx.agent
        if agent is None or not hasattr(agent, "load_state") or not hasattr(agent, "save_state"):
            return
        state = agent.load_state(ctx.session)
        if state.task_plan is None:
            return
        state.task_plan = None
        agent.save_state(ctx.session, state)

    @staticmethod
    def _iteration_interrupted(ctx: AgentCallbackContext) -> bool:
        inputs = ctx.inputs
        result = getattr(inputs, "result", None)
        return isinstance(result, dict) and result.get("result_type") == "interrupt"

    @staticmethod
    def _todo_to_task_item(todo: TodoItem) -> TaskItem:
        """Map a persisted TodoItem into a TaskPlan TaskItem."""
        if todo.status == TodoStatus.COMPLETED:
            task_status = TaskStatus.COMPLETED
        elif todo.status == TodoStatus.IN_PROGRESS:
            task_status = TaskStatus.IN_PROGRESS
        elif todo.status == TodoStatus.CANCELLED:
            task_status = TaskStatus.FAILED
        else:
            task_status = TaskStatus.PENDING
        return TaskItem(
            id=todo.id,
            title=todo.content,
            status=task_status,
        )


__all__ = [
    "ConcurrentSafeFileSystemRail",
    "ConcurrentSafeSysOperationRail",
    "ConcurrentSafeTaskPlanningRail",
]
