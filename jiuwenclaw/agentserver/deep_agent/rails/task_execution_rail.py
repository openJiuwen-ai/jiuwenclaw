# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ToolCallInputs
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.workspace.workspace import WorkspaceNode

from jiuwenclaw.utils import get_agent_sessions_dir

logger = logging.getLogger(__name__)

_ACTIVE_TASK_ID: ContextVar[str | None] = ContextVar("active_task_id", default=None)


def get_current_task_id() -> str | None:
    """Return current task id for stream payload correlation."""
    return _ACTIVE_TASK_ID.get()


@dataclass
class TaskExecutionContext:
    task_id: str
    task_content: str
    task_index: int
    total_tasks: int
    parent_request_id: str
    start_time: float
    source: Literal["todo", "skill_step"]
    status: Literal["running", "succeeded", "failed", "skipped"] = "running"


class TaskExecutionRail(DeepAgentRail):
    """Emit task.start/task.complete around todo and skill_step execution status transitions.

    Tool Categories:
    - TODO_TOOLS: todo_create, todo_list, todo_modify - triggers todo state change detection
    - SKILL_STEP_TOOLS: skill_step - triggers skill_step state change detection
    - BUSINESS_TOOLS: all other tools - auto-starts first pending skill_step task
    """

    priority = 85

    TODO_TOOLS = frozenset({"todo_create", "todo_list", "todo_modify"})
    SKILL_STEP_TOOLS = frozenset({"skill_step"})
    ALL_TASK_TOOLS = TODO_TOOLS | SKILL_STEP_TOOLS

    def __init__(self) -> None:
        super().__init__()
        self._todo_map: dict[str, dict[str, Any]] = {}
        self._todo_map_before_tool: dict[str, dict[str, Any]] = {}
        self._skill_step_map: dict[str, dict[str, Any]] = {}
        self._skill_step_map_before_tool: dict[str, dict[str, Any]] = {}
        self._active_tasks: dict[str, TaskExecutionContext] = {}
        self._todo_started: set[str] = set()
        self._skill_step_started: set[str] = set()
        self._deep_agent: Any | None = None
        self._current_task_id: str | None = None

    def get_current_task_id(self) -> str | None:
        return self._current_task_id

    def init(self, agent: Any) -> None:
        self._deep_agent = agent

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        self._todo_map = {}
        self._todo_map_before_tool = {}
        self._skill_step_map = {}
        self._skill_step_map_before_tool = {}
        self._active_tasks = {}
        self._todo_started = set()
        self._skill_step_started = set()
        self._current_task_id = None
        _ACTIVE_TASK_ID.set(None)
        if isinstance(ctx.inputs, InvokeInputs):
            await self._init_task_tracking(ctx.session)

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        tool_name = ctx.inputs.tool_name

        if tool_name in self.TODO_TOOLS:
            self._todo_map_before_tool = dict(self._todo_map)
            return

        if tool_name in self.SKILL_STEP_TOOLS:
            self._skill_step_map_before_tool = dict(self._skill_step_map)
            return

        await self._maybe_start_pending_skill_step_task(ctx)
        self._bind_context_to_in_progress_task()

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        tool_name = ctx.inputs.tool_name

        if tool_name in self.TODO_TOOLS:
            await self._sync_todo_and_emit_transitions(ctx)
            return

        if tool_name in self.SKILL_STEP_TOOLS:
            await self._sync_skill_step_and_emit_transitions(ctx)
            return

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        self._todo_map_before_tool = {}
        self._skill_step_map_before_tool = {}
        self._bind_context_to_in_progress_task()

    async def _init_task_tracking(self, session: Session | None) -> None:
        if session is None:
            return
        session_id = session.get_session_id()

        try:
            todo_items = self._load_todo_from_json(session_id)
            if todo_items:
                self._todo_map = self._build_map_from_todo_items(todo_items)
                logger.info("[TaskExecutionRail] Loaded todo.json: %d tasks", len(todo_items))
        except Exception as exc:
            logger.debug("[TaskExecutionRail] Failed to load todo.json: %s", exc)

        try:
            skill_step_items = self._load_skill_step_from_markdown(session_id)
            if skill_step_items:
                self._skill_step_map = self._build_map_from_skill_step_items(skill_step_items)
                logger.info("[TaskExecutionRail] Loaded skill_step.md: %d tasks", len(skill_step_items))
        except Exception as exc:
            logger.debug("[TaskExecutionRail] Failed to load skill_step.md: %s", exc)

    def _load_todo_from_json(self, session_id: str) -> list[dict[str, Any]]:
        todo_path = self._get_todo_workspace_path(session_id)
        if not todo_path.exists():
            return []
        with open(todo_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []

    def _load_skill_step_from_markdown(self, session_id: str) -> list[dict[str, Any]]:
        skill_step_path = self._get_skill_step_workspace_path(session_id)
        if not skill_step_path.exists():
            return []
        items: list[dict[str, Any]] = []
        with open(skill_step_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                status = self._parse_markdown_status(line)
                rest = (
                    line.replace("- [x]", "")
                    .replace("- [-]", "")
                    .replace("- [>]", "")
                    .replace("- [ ]", "")
                    .strip()
                )
                if "." in rest:
                    idx_str, _, task_text = rest.partition(".")
                    task_text = task_text.split("|")[0].strip()
                    try:
                        idx = int(idx_str.strip())
                        items.append({"idx": idx, "content": task_text, "status": status})
                    except ValueError:
                        pass
        return sorted(items, key=lambda x: x["idx"])

    def _parse_markdown_status(self, line: str) -> str:
        if "[-]" in line:
            return "cancelled"
        if "[>]" in line:
            return "in_progress"
        if "[x]" in line.lower() or "[√]" in line:
            return "completed"
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 2:
            status_str = parts[1].strip().lower()
            if status_str in ("running", "in_progress"):
                return "in_progress"
            if status_str in ("waiting", "pending"):
                return "pending"
            if status_str == "completed":
                return "completed"
            if status_str == "cancelled":
                return "cancelled"
        return "pending"

    def _build_map_from_todo_items(self, items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        mapped: dict[str, dict[str, Any]] = {}
        total = len(items)
        for index, item in enumerate(items):
            task_id = item.get("id", str(index))
            status = item.get("status", "pending")
            normalized_status = status.lower() if isinstance(status, str) else str(status).lower()
            mapped[task_id] = {
                "content": item.get("content", item.get("activeForm", "")),
                "status": normalized_status,
                "index": index,
                "total": total,
            }
        return mapped

    def _build_map_from_skill_step_items(self, items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        mapped: dict[str, dict[str, Any]] = {}
        total = len(items)
        for index, item in enumerate(items):
            task_id = str(item.get("idx", index))
            mapped[task_id] = {
                "content": item.get("content", ""),
                "status": item.get("status", "pending"),
                "index": index,
                "total": total,
            }
        return mapped

    async def _sync_todo_and_emit_transitions(self, ctx: AgentCallbackContext) -> None:
        if ctx.session is None:
            return
        session_id = ctx.session.get_session_id()
        parent_request_id = self._extract_request_id(ctx)

        try:
            todo_items = self._load_todo_from_json(session_id)
        except Exception as exc:
            logger.warning("[TaskExecutionRail] Failed to load todo.json: %s", exc)
            return

        current_map = self._build_map_from_todo_items(todo_items)
        previous_map = self._todo_map_before_tool or self._todo_map

        for task_id, current in current_map.items():
            prev = previous_map.get(task_id)
            prev_status = prev.get("status", "") if prev else ""
            curr_status = current.get("status", "")

            if curr_status == "in_progress" and prev_status not in ("in_progress", "completed"):
                if task_id not in self._todo_started:
                    await self._emit_task_start_event(
                        ctx.session, task_id, current, parent_request_id, source="todo"
                    )
                    self._todo_started.add(task_id)
            elif prev_status == "in_progress" and curr_status == "completed":
                await self._emit_task_complete_event(ctx.session, task_id, current, status="succeeded")

        self._todo_map = current_map
        self._todo_map_before_tool = {}
        self._bind_context_to_in_progress_task()

    async def _maybe_start_pending_skill_step_task(self, ctx: AgentCallbackContext) -> None:
        if ctx.session is None:
            return

        if not self._skill_step_map:
            return

        has_in_progress = any(
            task.get("status") == "in_progress" for task in self._skill_step_map.values()
        )
        if has_in_progress:
            return

        pending_task_id = None
        pending_task = None
        for task_id, task in self._skill_step_map.items():
            if task.get("status") == "pending":
                pending_task_id = task_id
                pending_task = task
                break

        if pending_task_id is None or pending_task_id in self._skill_step_started:
            return

        parent_request_id = self._extract_request_id(ctx)
        await self._emit_task_start_event(
            ctx.session, pending_task_id, pending_task, parent_request_id, source="skill_step"
        )
        self._skill_step_started.add(pending_task_id)
        self._skill_step_map[pending_task_id]["status"] = "in_progress"

    async def _sync_skill_step_and_emit_transitions(self, ctx: AgentCallbackContext) -> None:
        if ctx.session is None:
            return
        session_id = ctx.session.get_session_id()
        parent_request_id = self._extract_request_id(ctx)

        try:
            skill_step_items = self._load_skill_step_from_markdown(session_id)
        except Exception as exc:
            logger.warning("[TaskExecutionRail] Failed to load skill_step.md: %s", exc)
            return

        current_map = self._build_map_from_skill_step_items(skill_step_items)
        previous_map = self._skill_step_map_before_tool or self._skill_step_map

        for task_id, prev_task in self._skill_step_map.items():
            if prev_task.get("status") == "in_progress":
                curr_task = current_map.get(task_id)
                if curr_task and curr_task.get("status") == "pending":
                    current_map[task_id]["status"] = "in_progress"

        for task_id, current in current_map.items():
            prev = previous_map.get(task_id)
            prev_status = prev.get("status", "") if prev else ""
            curr_status = current.get("status", "")

            if curr_status == "completed" and prev_status in ("pending", "in_progress", ""):
                if task_id not in self._skill_step_started:
                    await self._emit_task_start_event(
                        ctx.session, task_id, current, parent_request_id, source="skill_step"
                    )
                    self._skill_step_started.add(task_id)
                await self._emit_task_complete_event(ctx.session, task_id, current, status="succeeded")

        self._skill_step_map = current_map
        self._skill_step_map_before_tool = {}
        self._bind_context_to_in_progress_task()

    async def _emit_task_start_event(
        self,
        session: Session,
        task_id: str,
        task: dict[str, Any],
        parent_request_id: str,
        source: Literal["todo", "skill_step"],
    ) -> None:
        full_task_id = f"{source}:{task_id}"

        if full_task_id in self._active_tasks:
            _ACTIVE_TASK_ID.set(full_task_id)
            return

        context = TaskExecutionContext(
            task_id=full_task_id,
            task_content=str(task.get("content", "")),
            task_index=int(task.get("index", 0)),
            total_tasks=int(task.get("total", 0)),
            parent_request_id=parent_request_id,
            start_time=time.time(),
            source=source,
        )
        self._active_tasks[full_task_id] = context
        _ACTIVE_TASK_ID.set(full_task_id)
        self._current_task_id = full_task_id

        logger.info("[TaskExecutionRail] task.start: %s - %s", full_task_id, context.task_content)

        await session.write_stream(
            OutputSchema(
                type="task.start",
                index=0,
                payload={
                    "task_id": context.task_id,
                    "task_content": context.task_content,
                    "task_index": context.task_index,
                    "total_tasks": context.total_tasks,
                    "parent_request_id": context.parent_request_id,
                    "timestamp": context.start_time,
                    "source": source,
                },
            )
        )

    async def _emit_task_complete_event(
        self,
        session: Session,
        task_id: str,
        task: dict[str, Any],
        *,
        status: Literal["succeeded", "failed", "skipped"],
        error: str | None = None,
    ) -> None:
        for source in ["todo", "skill_step"]:
            full_task_id = f"{source}:{task_id}"
            context = self._active_tasks.get(full_task_id)
            if context:
                break
        else:
            return

        timestamp = time.time()
        duration_ms = int((timestamp - context.start_time) * 1000)

        if get_current_task_id() == full_task_id:
            _ACTIVE_TASK_ID.set(None)
            self._current_task_id = None

        logger.info("[TaskExecutionRail] task.complete: %s - %s (%dms)", full_task_id, status, duration_ms)

        await session.write_stream(
            OutputSchema(
                type="task.complete",
                index=0,
                payload={
                    "task_id": context.task_id,
                    "task_content": context.task_content,
                    "status": status,
                    "duration_ms": duration_ms,
                    "error": error,
                    "timestamp": timestamp,
                    "source": context.source,
                },
            )
        )
        self._active_tasks.pop(full_task_id, None)

    def _bind_context_to_in_progress_task(self) -> None:
        # 优先绑定 skill_step 任务，再绑定 todo 任务
        for task_id, task in self._skill_step_map.items():
            if task.get("status") == "in_progress":
                full_task_id = f"skill_step:{task_id}"
                _ACTIVE_TASK_ID.set(full_task_id)
                self._current_task_id = full_task_id
                return

        for task_id, task in self._todo_map.items():
            if task.get("status") == "in_progress":
                full_task_id = f"todo:{task_id}"
                _ACTIVE_TASK_ID.set(full_task_id)
                self._current_task_id = full_task_id
                return

        _ACTIVE_TASK_ID.set(None)
        self._current_task_id = None

    def _get_todo_workspace_path(self, session_id: str) -> Path:
        if self.workspace is not None:
            return Path(self.workspace.get_node_path(WorkspaceNode.TODO)) / session_id / "todo.json"
        return get_agent_sessions_dir() / session_id / "todo.json"

    def _get_skill_step_workspace_path(self, session_id: str) -> Path:
        return get_agent_sessions_dir() / session_id / "skill_step.md"

    @staticmethod
    def _extract_request_id(ctx: AgentCallbackContext) -> str:
        value = getattr(ctx.inputs, "request_id", "")
        return str(value) if value else ""
