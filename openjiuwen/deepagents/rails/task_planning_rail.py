# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""TaskPlanningRail — registers todo tools on DeepAgent."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
)
from openjiuwen.deepagents.rails.base import DeepAgentRail
from openjiuwen.deepagents.schema.state import (
    load_state,
    save_state,
)
from openjiuwen.deepagents.schema.task import (
    TaskItem,
    TaskPlan,
    TaskStatus,
)
from openjiuwen.deepagents.tools.todo import (
    TodoStatus,
    TodoTool,
    create_todos_tool,
)


class TaskPlanningRail(DeepAgentRail):
    """Rail that registers todo tools on the agent.

    After the first task-loop iteration, bridges the
    LLM-created todo list into a ``TaskPlan`` so the
    outer loop can schedule subsequent steps.

    Attributes:
        priority: Execution priority (90 = high).
    """

    priority = 90

    def __init__(self, language: str = "cn") -> None:
        super().__init__()
        self.tools = None
        self.language = language

    def init(self, agent) -> None:
        """Register todo tools on the agent."""
        from openjiuwen.deepagents.deep_agent import (
            DeepAgent,
        )

        if not (
            isinstance(agent, DeepAgent)
            and agent.deep_config
            and hasattr(agent, "ability_manager")
        ):
            return

        self.workspace = getattr(
            agent.deep_config, "workspace", None
        )
        workspace_path = None
        if self.workspace is not None:
            workspace_path = (
                self.workspace.root_path
                if hasattr(self.workspace, "root_path")
                else str(self.workspace)
            )
        tools = create_todos_tool(
            self.sys_operation,
            workspace_path,
            self.language,
        )
        self.tools = tools
        Runner.resource_mgr.add_tool(list(tools))
        for tool in tools:
            agent.ability_manager.add(tool.card)

    def uninit(self, agent) -> None:
        """Remove todo tools from the agent."""
        if self.tools and hasattr(agent, "ability_manager"):
            for tool in self.tools:
                name = getattr(tool, "name", None)
                if name:
                    agent.ability_manager.remove(name)
                tool_id = tool.card.id
                if tool_id:
                    Runner.resource_mgr.remove_tool(tool_id)

    # -- task-loop hook --

    async def after_task_iteration(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Bridge todo list to TaskPlan after iteration."""
        await self._bridge_todos_to_plan(ctx)
        await self._sync_todos_from_plan(ctx)

    # -- internal helpers --

    async def _bridge_todos_to_plan(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Convert LLM-created todos into a TaskPlan.

        Guards:
            - ctx.session must exist
            - state.task_plan must be empty (no re-entry)
            - A TodoTool must be registered
            - At least one PENDING todo must exist
        """
        if ctx.session is None:
            return

        state = load_state(ctx)

        if (
            state.task_plan is not None
            and len(state.task_plan.tasks) > 0
        ):
            return

        tool = self._find_todo_tool()
        if tool is None:
            return

        session_id = ctx.session.get_session_id()
        tool.file = f"{session_id}.json"

        try:
            todos = await tool.load_todos()
        except Exception:
            logger.debug(
                "TaskPlanningRail: no todos to bridge"
            )
            return

        if not todos:
            return

        has_pending = any(
            t.status == TodoStatus.PENDING for t in todos
        )
        if not has_pending:
            return

        plan = TaskPlan(goal="bridged from todo list")
        for todo in todos:
            if todo.status in (
                TodoStatus.IN_PROGRESS,
                TodoStatus.COMPLETED,
            ):
                task_status = TaskStatus.COMPLETED
            else:
                task_status = TaskStatus.PENDING

            plan.add_task(
                TaskItem(
                    id=todo.id,
                    title=todo.content,
                    status=task_status,
                )
            )

        state.task_plan = plan
        save_state(ctx, state)
        logger.info(
            "TaskPlanningRail: bridged %d todos "
            "into TaskPlan (%s)",
            len(todos),
            plan.get_progress_summary(),
        )

    async def _sync_todos_from_plan(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Sync Todo file statuses from current TaskPlan.

        This keeps todo persistence and task-plan status aligned.
        Without this sync, a task can be marked completed in
        TaskPlan while still being ``in_progress`` in todo file,
        which later causes todo validation conflicts.
        """
        if ctx.session is None:
            return

        state = load_state(ctx)
        plan = state.task_plan
        if plan is None or len(plan.tasks) == 0:
            return

        tool = self._find_todo_tool()
        if tool is None:
            return

        session_id = ctx.session.get_session_id()
        tool.file = f"{session_id}.json"

        try:
            todos = await tool.load_todos()
        except Exception:
            logger.debug(
                "TaskPlanningRail: no todos for sync"
            )
            return

        if not todos:
            return

        status_by_task_id = {
            task.id: self._to_todo_status(task.status)
            for task in plan.tasks
        }
        changed = False
        now = datetime.now(timezone.utc).isoformat()

        for todo in todos:
            desired = status_by_task_id.get(todo.id)
            if desired is None:
                continue
            if todo.status != desired:
                todo.status = desired
                todo.updatedAt = now
                changed = True

        if not changed:
            return

        await tool.save_todos(todos)
        logger.info(
            "TaskPlanningRail: synced %d todos from TaskPlan",
            len(todos),
        )

    @staticmethod
    def _to_todo_status(status: TaskStatus) -> TodoStatus:
        """Map TaskPlan status to Todo status."""
        if status == TaskStatus.PENDING:
            return TodoStatus.PENDING
        if status == TaskStatus.IN_PROGRESS:
            return TodoStatus.IN_PROGRESS
        # TodoStatus lacks FAILED; map terminal states to COMPLETED.
        return TodoStatus.COMPLETED

    def _find_todo_tool(self) -> Optional[TodoTool]:
        """Return the first TodoTool in self.tools."""
        if not self.tools:
            return None
        for tool in self.tools:
            if isinstance(tool, TodoTool):
                return tool
        return None


__all__ = [
    "TaskPlanningRail",
]
