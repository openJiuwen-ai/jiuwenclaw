# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TaskExecutionRail — Emit task.start/task.complete/task.update lifecycle events.

Tracks todo status transitions (pending->in_progress->completed) and emits
lifecycle events to the frontend. Binds the current task_id via ContextVar
so downstream tool/artifact events can be attributed to the active task.
"""
from __future__ import annotations

import json
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ToolCallInputs,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.workspace.workspace import WorkspaceNode

from jiuwenswarm.common.utils import logger

_ACTIVE_TASK_ID: ContextVar[str | None] = ContextVar(
    "active_task_id", default=None
)


def get_current_task_id() -> str | None:
    """Return current task id for stream payload correlation."""
    return _ACTIVE_TASK_ID.get()


# 图像产物扩展名白名单
_IMAGE_ARTIFACT_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
})

# 文件路径检测的正则表达式模式（仿 PR#1440，配合图像扩展名白名单过滤）
_IMAGE_FILE_PATH_PATTERNS = [
    # Windows绝对路径 (D:\path, D:/path)
    re.compile(r'[A-Za-z]:[/\\][^\s\]\}\)\,\'\"`<>，。；、：]+'),
    # Unix绝对路径 (/path/to/file)
    re.compile(r'/[^\s\]\}\)\,\'\"`<>，。；、：]+'),
    # 相对路径带有扩展名 (./path, path/file.ext)
    re.compile(
        r'(?<![/\\])(?:\.{1,2}[/\\])?(?:[^\s\]\}\)\,\'\"`<>，。；、：]+[/\\])+'
        r'[^\s\]\}\)\,\'\"`<>，。；、：]+\.[a-zA-Z0-9]{1,10}'
    ),
]

_PATH_TRAILING_CHARS = "'\"`\\]\\}\\),.;:，。；、："


def _clean_path_candidate(path_str: str) -> str:
    """清理正则提取到的路径候选首尾非法字符。"""
    return path_str.strip().strip(_PATH_TRAILING_CHARS).strip()


def _extract_image_paths_from_tool_result(tool_result: Any) -> list[str]:
    """从工具输出结果中提取图像产物路径。

    仿 PR#1440 的正则提取思路，范围限定为图像扩展名白名单。
    处理字符串、字典、对象三类结果。
    """
    if tool_result is None:
        return []

    if isinstance(tool_result, str):
        result_text = tool_result
    elif isinstance(tool_result, dict):
        result_text = json.dumps(tool_result, ensure_ascii=False)
    elif hasattr(tool_result, "__dict__"):
        result_text = str(tool_result)
    else:
        result_text = str(tool_result)

    seen: set[str] = set()
    paths: list[str] = []
    for pattern in _IMAGE_FILE_PATH_PATTERNS:
        for match in pattern.findall(result_text):
            cleaned = _clean_path_candidate(match)
            if not cleaned:
                continue
            identity = cleaned.replace("\\", "/").lower()
            if identity in seen:
                continue
            if Path(cleaned).suffix.lower() not in _IMAGE_ARTIFACT_EXTENSIONS:
                continue
            seen.add(identity)
            paths.append(cleaned)

    return paths


@dataclass
class TaskExecutionContext:
    task_id: str
    task_content: str
    task_index: int
    total_tasks: int
    parent_request_id: str
    start_time: float
    source: Literal["todo"]
    status: Literal["running", "succeeded", "failed", "skipped"] = "running"


class TaskExecutionRail(DeepAgentRail):
    """Emit task.start/task.complete/task.update around todo execution transitions.

    TODO_TOOLS (todo_create, todo_modify, todo_list, todo_get) trigger todo
    state change detection via _sync_todo_and_emit_transitions. Non-todo tools
    bind the current in-progress todo task via the _ACTIVE_TASK_ID ContextVar.
    """

    _BINDING_IN_PROGRESS = frozenset({"in_progress"})
    _BINDING_PENDING = frozenset({"pending", "waiting"})
    _TODO_DONE_STATUSES = frozenset({"completed", "cancelled"})

    priority = 85

    TODO_TOOLS = frozenset({
        "todo_create", "todo_get", "todo_list", "todo_modify",
    })

    # 触发图像产物后处理 hook 的工具
    IMAGE_TOOLS = frozenset({"generate_image"})

    def __init__(self) -> None:
        super().__init__()
        self._todo_map: dict[str, dict[str, Any]] = {}
        self._todo_map_before_tool: dict[str, dict[str, Any]] = {}
        self._active_tasks: dict[str, TaskExecutionContext] = {}
        self._todo_started: set[str] = set()
        self._deep_agent: Any | None = None

    def get_current_task_id(self) -> str | None:
        return _ACTIVE_TASK_ID.get()

    def init(self, agent: Any) -> None:
        self._deep_agent = agent

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        session_id = ""
        if ctx.session is not None:
            try:
                session_id = str(ctx.session.get_session_id() or "")
            except Exception:
                logger.debug(
                    "[TaskExecutionRail] before_invoke: "
                    "failed to get session_id",
                    exc_info=True,
                )
        logger.info(
            "[TaskExecutionRail] before_invoke reset tracking: "
            "session_id=%s prev_todo_map_size=%d prev_active_tasks=%s",
            session_id,
            len(self._todo_map),
            list(self._active_tasks.keys()),
        )
        self._todo_map = {}
        self._todo_map_before_tool = {}
        self._active_tasks = {}
        self._todo_started = set()
        _ACTIVE_TASK_ID.set(None)
        if isinstance(ctx.inputs, InvokeInputs):
            await self._init_task_tracking(ctx.session)
            has_active_tasks = any(
                t.get("status") in ("pending", "in_progress")
                for t in self._todo_map.values()
            )
            if has_active_tasks:
                parent_request_id = self._extract_request_id(ctx)
                await self._emit_task_update_event(
                    ctx.session, parent_request_id
                )
        self._bind_context_to_in_progress_task()

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Bind task_id before LLM calls."""
        self._bind_context_to_in_progress_task()

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        tool_name = ctx.inputs.tool_name

        if tool_name in self.TODO_TOOLS:
            session_id = ""
            if ctx.session is not None:
                try:
                    session_id = str(
                        ctx.session.get_session_id() or ""
                    )
                except Exception:
                    logger.debug(
                        "[TaskExecutionRail] before_tool_call: "
                        "failed to get session_id",
                        exc_info=True,
                    )
            logger.info(
                "[TaskExecutionRail] todo snapshot before_tool: "
                "session=%s todo_map_size=%d active_tasks=%s",
                session_id,
                len(self._todo_map),
                list(self._active_tasks.keys()),
            )
            self._todo_map_before_tool = dict(self._todo_map)
            return

        self._bind_context_to_in_progress_task()

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not isinstance(ctx.inputs, ToolCallInputs):
            return
        tool_name = ctx.inputs.tool_name

        if tool_name in self.TODO_TOOLS:
            await self._sync_todo_and_emit_transitions(ctx)
            return

        if tool_name in self.IMAGE_TOOLS:
            await self._trigger_image_artifact_hook(ctx)
            return

    async def _trigger_image_artifact_hook(
        self, ctx: AgentCallbackContext
    ) -> None:
        """图像产物落盘后触发 IMAGE_ARTIFACT_POST_PROCESS 扩展 hook。

        从 tool_result 解析图像路径，构建 ImageArtifactHookContext 并触发
        扩展回调；扩展可在 handler 中对文件做原地后处理（如加水印）。
        ExtensionRegistry 未初始化或扩展抛错时仅记 warning，不阻断主流程。
        """
        session = ctx.session
        if session is None:
            return
        try:
            session_id = session.get_session_id()
        except Exception:
            logger.debug(
                "[TaskExecutionRail] image artifact hook: "
                "failed to get session_id",
                exc_info=True,
            )
            return

        tool_result = getattr(ctx.inputs, "tool_result", None)
        image_paths = _extract_image_paths_from_tool_result(tool_result)
        if not image_paths:
            return

        task_id = _ACTIVE_TASK_ID.get()
        tool_name = ctx.inputs.tool_name

        try:
            from jiuwenswarm.extensions.registry import ExtensionRegistry
            from jiuwenswarm.extensions.hook_event import (
                AgentServerHookEvents,
            )
            from jiuwenswarm.extensions.hooks_context import (
                ImageArtifactHookContext,
            )
        except ImportError as exc:
            logger.warning(
                "[TaskExecutionRail] skip image artifact hook, "
                "import failed: %s",
                exc,
            )
            return

        hook_ctx = ImageArtifactHookContext(
            session_id=session_id,
            tool_name=tool_name,
            task_id=task_id,
            artifact_paths=image_paths,
        )
        try:
            await ExtensionRegistry.get_instance().trigger(
                AgentServerHookEvents.IMAGE_ARTIFACT_POST_PROCESS,
                hook_ctx,
            )
        except RuntimeError:
            logger.warning(
                "[TaskExecutionRail] skip image artifact hook: "
                "ExtensionRegistry not initialized",
            )
            return
        except Exception as exc:
            logger.warning(
                "[TaskExecutionRail] image artifact hook failed "
                "session_id=%s tool=%s error=%s",
                session_id,
                tool_name,
                exc,
            )
            return

        logger.info(
            "[TaskExecutionRail] image artifact hook done "
            "session_id=%s tool=%s count=%d",
            session_id,
            tool_name,
            len(image_paths),
        )

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        self._todo_map_before_tool = {}
        self._bind_context_to_in_progress_task()

    # ------------------------------------------------------------------
    # Task list state loading
    # ------------------------------------------------------------------

    async def _init_task_tracking(
        self, session: Session | None
    ) -> None:
        if session is None:
            return
        session_id = session.get_session_id()
        try:
            todo_items = self._load_todo_from_json(session_id)
            if todo_items:
                self._todo_map = self._build_map_from_todo_items(
                    todo_items
                )
                logger.info(
                    "[TaskExecutionRail] Loaded todo.json "
                    "session_id=%s tasks=%d",
                    session_id,
                    len(todo_items),
                )
        except Exception as exc:
            logger.debug(
                "[TaskExecutionRail] Failed to load todo.json: %s",
                exc,
            )

    def _load_todo_from_json(
        self, session_id: str
    ) -> list[dict[str, Any]]:
        todo_path = self._get_todo_workspace_path(session_id)
        if todo_path is None or not todo_path.exists():
            return []
        with open(todo_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []

    def _get_todo_workspace_path(
        self, session_id: str
    ) -> Path | None:
        """Resolve todo.json path from the deep agent's workspace config."""
        da = self._deep_agent
        if da is None:
            return None
        try:
            deep_config = da.deep_config
            workspace_path = Path(
                deep_config.workspace.get_node_path(WorkspaceNode.TODO)
            )
            return workspace_path / session_id / "todo.json"
        except Exception as exc:
            logger.debug(
                "[TaskExecutionRail] Failed to resolve todo "
                "workspace path: %s",
                exc,
            )
            return None

    def _build_map_from_todo_items(
        self, items: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        mapped: dict[str, dict[str, Any]] = {}
        total = len(items)
        for index, item in enumerate(items):
            task_id = item.get("id", str(index))
            status = item.get("status", "pending")
            if isinstance(status, str):
                normalized_status = status.lower()
            else:
                normalized_status = str(status).lower()
            mapped[task_id] = {
                "content": item.get(
                    "content", item.get("activeForm", "")
                ),
                "status": normalized_status,
                "index": index,
                "total": total,
            }
        return mapped

    @staticmethod
    def _has_incomplete_todos(
        todo_map: dict[str, dict[str, Any]]
    ) -> bool:
        if not todo_map:
            return False
        return any(
            str(task.get("status", "pending")).lower()
            not in TaskExecutionRail._TODO_DONE_STATUSES
            for task in todo_map.values()
        )

    # ------------------------------------------------------------------
    # State transition detection + event emission
    # ------------------------------------------------------------------

    async def _sync_todo_and_emit_transitions(
        self, ctx: AgentCallbackContext
    ) -> None:
        """Diff todo state before vs after a todo tool call and emit events.

        - pending -> in_progress  => task.start
        - in_progress -> completed => task.complete
        Always emits task.update (full snapshot) at the end.
        """
        if ctx.session is None:
            return
        session_id = ctx.session.get_session_id()
        parent_request_id = self._extract_request_id(ctx)

        try:
            todo_items = self._load_todo_from_json(session_id)
        except Exception as exc:
            logger.warning(
                "[TaskExecutionRail] Failed to load todo.json: %s",
                exc,
            )
            return

        current_map = self._build_map_from_todo_items(todo_items)
        previous_map = self._todo_map_before_tool or self._todo_map

        completed_in_batch: list[str] = []
        for task_id, current in current_map.items():
            prev = previous_map.get(task_id)
            prev_status = prev.get("status", "") if prev else ""
            curr_status = current.get("status", "")

            if (
                curr_status == "in_progress"
                and prev_status not in ("in_progress", "completed")
            ):
                if task_id not in self._todo_started:
                    await self._emit_task_start_event(
                        ctx.session,
                        task_id,
                        current,
                        parent_request_id,
                        source="todo",
                    )
                    self._todo_started.add(task_id)
            elif (
                curr_status == "completed"
                and prev_status != "completed"
            ):
                completed_in_batch.append(task_id)
                if prev_status == "in_progress":
                    await self._emit_task_complete_event(
                        ctx.session,
                        task_id,
                        current,
                        status="succeeded",
                        parent_request_id=parent_request_id,
                    )
                else:
                    logger.info(
                        "[TaskExecutionRail] skip task.complete "
                        "(gate1): %s prev_status=%r "
                        "curr_status=%r session_id=%s",
                        task_id,
                        prev_status,
                        curr_status,
                        session_id,
                    )

        self._todo_map = current_map
        self._todo_map_before_tool = {}
        self._bind_context_after_todo_sync(
            completed_in_batch, current_map
        )
        await self._emit_task_update_event(
            ctx.session, parent_request_id
        )

    async def _emit_task_start_event(
        self,
        session: Session,
        task_id: str,
        task: dict[str, Any],
        parent_request_id: str,
        source: Literal["todo"],
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

        logger.info(
            "[TaskExecutionRail] task.start: %s - %s",
            full_task_id,
            context.task_content,
        )

        try:
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
        except Exception:
            logger.debug(
                "[TaskExecutionRail] task.start emit failed",
                exc_info=True,
            )

    async def _emit_task_complete_event(
        self,
        session: Session,
        task_id: str,
        task: dict[str, Any],
        *,
        status: Literal["succeeded", "failed", "skipped"],
        error: str | None = None,
        parent_request_id: str = "",
    ) -> None:
        full_task_id = f"todo:{task_id}"
        context = self._active_tasks.get(full_task_id)
        timestamp = time.time()

        if context:
            duration_ms = int(
                (timestamp - context.start_time) * 1000
            )
            payload_task_id = context.task_id
            task_content = context.task_content
            source = context.source
            self._active_tasks.pop(full_task_id, None)
        else:
            duration_ms = 0
            payload_task_id = full_task_id
            task_content = str(task.get("content", ""))
            source = "todo"

        if get_current_task_id() == full_task_id:
            _ACTIVE_TASK_ID.set(None)

        logger.info(
            "[TaskExecutionRail] task.complete: %s - %s (%dms)",
            full_task_id,
            status,
            duration_ms,
        )

        try:
            await session.write_stream(
                OutputSchema(
                    type="task.complete",
                    index=0,
                    payload={
                        "task_id": payload_task_id,
                        "task_content": task_content,
                        "status": status,
                        "duration_ms": duration_ms,
                        "error": error,
                        "timestamp": timestamp,
                        "source": source,
                        "parent_request_id": parent_request_id,
                    },
                )
            )
        except Exception:
            logger.debug(
                "[TaskExecutionRail] task.complete emit failed",
                exc_info=True,
            )

    async def _emit_task_update_event(
        self,
        session: Session,
        parent_request_id: str | None = None,
    ) -> None:
        """Send full task list snapshot (all todos) to the frontend."""
        session_id = session.get_session_id()
        todo_items = self._load_todo_from_json(session_id)
        todo_tasks = self._format_tasks_for_update(
            todo_items, source="todo"
        )

        all_tasks = todo_tasks
        total = len(all_tasks)
        completed = sum(
            1 for t in all_tasks
            if t.get("status") == "completed"
        )
        in_progress = sum(
            1 for t in all_tasks
            if t.get("status") == "in_progress"
        )
        pending = sum(
            1 for t in all_tasks
            if t.get("status") == "pending"
        )

        payload: dict[str, Any] = {
            "tasks": all_tasks,
            "total_tasks": total,
            "completed_tasks": completed,
            "in_progress_tasks": in_progress,
            "pending_tasks": pending,
            "timestamp": time.time(),
        }

        if parent_request_id:
            payload["parent_request_id"] = parent_request_id

        try:
            await session.write_stream(
                OutputSchema(
                    type="task.update",
                    index=0,
                    payload=payload,
                )
            )
        except Exception:
            logger.debug(
                "[TaskExecutionRail] task.update emit failed",
                exc_info=True,
            )

        logger.info(
            "[TaskExecutionRail] task.update: %d tasks - "
            "%d completed, %d in_progress, %d pending",
            total,
            completed,
            in_progress,
            pending,
        )

    def _format_tasks_for_update(
        self,
        items: list[dict[str, Any]],
        source: Literal["todo"],
    ) -> list[dict[str, Any]]:
        """Format todo items into task dicts for task.update payload."""
        formatted: list[dict[str, Any]] = []
        for item in items:
            task_id = str(
                item.get("id", item.get("idx", ""))
            )
            task: dict[str, Any] = {
                "task_id": task_id,
                "task_content": item.get(
                    "content", item.get("activeForm", "")
                ),
                "task_index": item.get(
                    "index", item.get("idx", 0)
                ),
                "source": source,
                "status": item.get("status", "pending"),
            }
            full_task_id = f"{source}:{task_id}"
            context = self._active_tasks.get(full_task_id)
            if context:
                task["start_time"] = context.start_time
            formatted.append(task)
        return formatted

    # ------------------------------------------------------------------
    # Task binding (ContextVar management)
    # ------------------------------------------------------------------

    def _task_candidates_by_status(
        self,
        allowed: frozenset[str],
    ) -> list[tuple[int, str]]:
        candidates: list[tuple[int, str]] = []
        for task_id, task in self._todo_map.items():
            if str(task.get("status", "")).lower() in allowed:
                candidates.append(
                    (int(task.get("index", 0)), task_id)
                )
        candidates.sort(key=lambda item: (item[0], item[1]))
        return candidates

    def _pick_task_id_for_binding(self) -> str | None:
        """Pick the first in_progress task, else first pending."""
        active = self._task_candidates_by_status(
            self._BINDING_IN_PROGRESS
        )
        if active:
            return active[0][1]
        pending = self._task_candidates_by_status(
            self._BINDING_PENDING
        )
        if pending:
            return pending[0][1]
        return None

    def _pick_next_pending_after(
        self, completed_task_id: str
    ) -> str | None:
        completed = self._todo_map.get(completed_task_id)
        if not completed:
            return self._pick_task_id_for_binding()
        completed_index = int(completed.get("index", 0))
        pending: list[tuple[int, str]] = []
        for task_id, task in self._todo_map.items():
            if (
                str(task.get("status", "")).lower()
                in self._BINDING_PENDING
            ):
                task_index = int(task.get("index", 0))
                if task_index > completed_index:
                    pending.append((task_index, task_id))
        if not pending:
            return None
        pending.sort(key=lambda item: (item[0], item[1]))
        return pending[0][1]

    def _set_active_task_binding(
        self, raw_task_id: str | None
    ) -> None:
        if raw_task_id:
            full_task_id = f"todo:{raw_task_id}"
            _ACTIVE_TASK_ID.set(full_task_id)
            logger.debug(
                "[TaskExecutionRail] task_id binding: %s",
                full_task_id,
            )
            return
        _ACTIVE_TASK_ID.set(None)

    def _bind_context_after_todo_sync(
        self,
        completed_in_batch: list[str],
        current_map: dict[str, dict[str, Any]],
    ) -> None:
        """Re-bind task_id after todo.json changed.

        in_progress wins over 'next pending after completed' so S3
        in_progress + S4 pending does not bind to S4 when S1/S2 complete
        in the same batch.
        """
        in_progress = self._task_candidates_by_status(
            self._BINDING_IN_PROGRESS
        )
        if in_progress:
            self._set_active_task_binding(in_progress[0][1])
            return
        if completed_in_batch:
            anchor_id = max(
                completed_in_batch,
                key=lambda tid: int(
                    current_map.get(tid, {}).get("index", 0)
                ),
            )
            next_id = self._pick_next_pending_after(anchor_id)
            if next_id:
                self._set_active_task_binding(next_id)
                return
        self._bind_context_to_in_progress_task()

    def _bind_context_to_in_progress_task(self) -> None:
        """Bind stream/artifact task_id to in_progress, else first pending."""
        self._set_active_task_binding(
            self._pick_task_id_for_binding()
        )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_request_id(ctx: AgentCallbackContext) -> str:
        value = getattr(ctx.inputs, "request_id", "")
        return str(value) if value else ""
