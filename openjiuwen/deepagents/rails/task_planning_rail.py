# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""TaskPlanningRail — registers todo tools on DeepAgent."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, List

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.message import UserMessage
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
)
from openjiuwen.deepagents.prompts.sections.todo import (
    build_progress_reminder_user_prompt,
    build_todo_section,
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
    TodoItem,
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

    def __init__(
        self,
        language: str = "cn",
        enable_progress_repeat: bool = False,
        list_tool_call_interval: int = 20,
    ) -> None:
        """Initialize TaskPlanningRail.

        Args:
            language: Language for prompts ('cn' or 'en').
            enable_progress_repeat: Check if progress repeat is enabled.
            list_tool_call_interval: Interval for progress reminder prompts (default: 20).
        """
        super().__init__()
        self.tools = None
        self.language = language
        self.enable_progress_repeat = enable_progress_repeat
        self.list_tool_call_interval = list_tool_call_interval
        self._tool_call_counts = {}
        self.system_prompt_builder = None

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

        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

        if not self.sys_operation:
            self.set_sys_operation(agent.deep_config.sys_operation)
        if not self.workspace:
            self.set_workspace(agent.deep_config.workspace)

        tools = create_todos_tool(
            self.sys_operation,
            self.workspace.root_path,
            self.language,
        )
        self.tools = tools
        try:
            Runner.resource_mgr.add_tool(list(tools))
            for tool in tools:
                agent.ability_manager.add(tool.card)
        except Exception as exc:
            logger.warning("TaskPlanningRail: failed to add tool, error: %s", exc)

    def uninit(self, agent) -> None:
        """Remove todo tools from the agent."""
        try:
            if self.tools and hasattr(agent, "ability_manager"):
                for tool in self.tools:
                    name = getattr(tool, "name", None)
                    if name:
                        agent.ability_manager.remove(name)
                    tool_id = tool.card.id
                    if tool_id:
                        Runner.resource_mgr.remove_tool(tool_id)
        except Exception as exc:
            logger.warning("TaskPlanningRail: failed to remove tool, error: %s", exc)

    # -- hook methods --
    async def before_model_call(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Inject task planning system prompt before model call."""
        if self.system_prompt_builder is None:
            return

        task_planning_section = build_todo_section(language=self.language)
        if task_planning_section is not None:
            self.system_prompt_builder.add_section(task_planning_section)
        else:
            self.system_prompt_builder.remove_section("todo")

    async def after_tool_call(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Add progress reminder prompt after tool call.

        Every N tool calls (configurable via tool_call_interval), adds a user
        message prompting the model to review current task progress using
        todo_list tool.

        Args:
            ctx: Agent callback context containing inputs and messages.
        """
        if not self.enable_progress_repeat or not ctx.session or not ctx.context:
            return

        tool = self._find_todo_tool()
        if tool is None:
            return

        session_id = ctx.session.get_session_id()
        if session_id not in self._tool_call_counts:
            self._tool_call_counts[session_id] = 0

        self._tool_call_counts[session_id] += 1
        if self._tool_call_counts[session_id] % self.list_tool_call_interval != 0:
            return

        tool.set_file(session_id)

        try:
            todos = await tool.load_todos()
        except Exception:
            logger.debug("TaskPlanningRail: after tool call load todos failed")
            return

        if not todos:
            return

        tasks, in_progress_task = self._format_task_content(todos)
        prompt = build_progress_reminder_user_prompt(
            language=self.language,
            tasks=tasks,
            in_progress_task=in_progress_task,
        )
        messages = ctx.context.get_messages()
        messages.append(UserMessage(content=prompt))
        ctx.context.set_messages(messages)

    async def after_invoke(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Clean up tool call count after agent invoke"""
        if ctx.session is None:
            return
        session_id = ctx.session.get_session_id()
        if session_id in self._tool_call_counts:
            del self._tool_call_counts[session_id]

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
        tool.set_file(session_id)

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
            elif todo.status == TodoStatus.CANCELLED:
                task_status = TaskStatus.FAILED
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
        tool.set_file(session_id)

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
        if status == TaskStatus.FAILED:
            return TodoStatus.CANCELLED
        return TodoStatus.COMPLETED

    def _find_todo_tool(self) -> Optional[TodoTool]:
        """Return the first TodoTool in self.tools."""
        if not self.tools:
            return None
        for tool in self.tools:
            if isinstance(tool, TodoTool):
                return tool
        return None

    def _format_task_content(self, todos: List[TodoItem]):
        """Format todos into a readable task content string.

        Args:
            todos: List of TodoItem objects to format.

        Returns:
            A tuple of (tasks, in_progress_task) where:
            - tasks: String showing all tasks with id, status, and content
            - in_progress_task: String content of the currently executing task (empty if none)
        """
        todos_str = []
        in_progress_str = ""
        for todo in todos:
            if todo.status == TodoStatus.IN_PROGRESS:
                in_progress_str = todo.content
            line = f"id: {todo.id} |status: {todo.status} |content: {todo.content}"
            todos_str.append(line)

        return "\n".join(todos_str), in_progress_str


__all__ = [
    "TaskPlanningRail",
]
