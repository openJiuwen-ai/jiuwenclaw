# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Compatibility shims for OpenJiuWen todo tools."""

from __future__ import annotations

import contextvars
import json
import os
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.harness.schema.task import TodoItem, TodoStatus
from openjiuwen.harness.tools.todo import TodoModifyTool as _OpenJiuWenTodoModifyTool

# Task-local flag: when set, the current coroutine already holds the session
# lock for an atomic read-modify-write, so load_todos/save_todos must use the
# lock-free file helpers instead of re-acquiring it (asyncio.Lock is
# non-reentrant → deadlock). Used by CompatibleTodoModifyTool.invoke to make
# the whole RMW share one critical section with skill_complete auto-flush.
_IN_LOCKED_RMW: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "todo_modify_in_locked_rmw", default=False
)


class CompatibleTodoModifyTool(_OpenJiuWenTodoModifyTool):
    """Accept deleted/canceled status updates from clients as todo mutations.

    Some clients send task deletion as an update payload with
    ``{"status": "deleted"}`` rather than the canonical
    ``{"action": "delete", "ids": [...]}``. The upstream tool rejects that
    status, leaving the task pending. This shim preserves the canonical behavior
    while treating those update statuses as delete/cancel operations.

    Additionally makes the whole read-modify-write atomic: the upstream
    ``invoke`` does ``load_todos`` (acquires+releases the session lock) then
    ``save_todos`` (re-acquires), so a concurrent writer — notably the
    TaskExecutionRail ``skill_complete`` auto-flush — can persist between our
    load and save, then our stale-snapshot save reverts it (lost update). We
    wrap the entire RMW in one ``lock_manager.operation(session_id)`` and route
    the inner load/save through lock-free helpers (gated by _IN_LOCKED_RMW) so
    the base dispatch logic is reused without re-acquiring the lock.
    """

    async def invoke(self, inputs: Input, **kwargs) -> Output:  # type: ignore[override]
        session = kwargs.get("session", None)
        session_id = (
            session.get_session_id()
            if session and hasattr(session, "get_session_id")
            else None
        )
        if session_id is None:
            raise build_error(
                StatusCode.TOOL_TODOS_INVOKE_FAILED,
                reason="Session ID is required",
            )
        # Hold the session lock across load+modify+save so the whole RMW is
        # mutually exclusive with any other todo_* writer sharing this lock
        # manager (skill_complete auto-flush, concurrent todo_modify).
        async with self._lock_manager.operation(session_id):
            token = _IN_LOCKED_RMW.set(True)
            try:
                return await super().invoke(inputs, **kwargs)
            finally:
                _IN_LOCKED_RMW.reset(token)

    async def load_todos(self, session_id: str) -> list[TodoItem]:  # type: ignore[override]
        if _IN_LOCKED_RMW.get():
            return await self._load_todos_unlocked(session_id)
        return await super().load_todos(session_id)

    async def save_todos(  # type: ignore[override]
        self, session_id: str, todos: list[TodoItem]
    ) -> None:
        if _IN_LOCKED_RMW.get():
            await self._save_todos_unlocked(session_id, todos)
            return
        await super().save_todos(session_id, todos)

    async def _load_todos_unlocked(self, session_id: str) -> list[TodoItem]:
        """Lock-free mirror of TodoTool.load_todos; the session lock is already
        held by our atomic invoke. Kept in sync with the upstream I/O path."""
        file_path = self._get_file_path(session_id)
        abs_path = os.path.abspath(file_path)
        if not os.path.isfile(abs_path):
            raise build_error(
                StatusCode.TOOL_TODOS_LOAD_FAILED,
                reason=f"Todo file not found: {abs_path}",
            )
        read_res = await self.fs.read_file(abs_path, mode="text")
        if read_res.code != 0:
            raise build_error(
                StatusCode.TOOL_TODOS_LOAD_FAILED,
                reason="Failed to load todo list, because read_file fail",
            )
        data = json.loads(read_res.data.content)
        return [TodoItem.from_dict(item) for item in data]

    async def _save_todos_unlocked(
        self, session_id: str, todos: list[TodoItem]
    ) -> None:
        """Lock-free mirror of TodoTool.save_todos; the session lock is already
        held by our atomic invoke. Kept in sync with the upstream I/O path."""
        file_path = self._get_file_path(session_id)
        abs_path = os.path.abspath(file_path)
        data = [todo.to_dict() for todo in todos]
        json_content = json.dumps(data, ensure_ascii=False, indent=2)
        write_res = await self.fs.write_file(abs_path, json_content, mode="text")
        if write_res.code != 0:
            raise build_error(
                StatusCode.TOOL_TODOS_SAVE_FAILED,
                reason="Failed to save todo list, because write_file fail",
            )

    async def _update_todos(
        self,
        session_id: str,
        todos_data: list[dict[str, Any]],
        current_todos: list[TodoItem],
    ) -> str:
        if not isinstance(todos_data, list):
            raise build_error(
                StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                reason="Batch update failed: 'todos' must be a list",
            )

        todo_map = {todo.id: todo for todo in current_todos}
        deleted_ids: set[str] = set()
        updated_count = 0

        for todo_data in todos_data:
            todo_id = todo_data.get("id")
            if not todo_id:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason="Batch update failed: Missing required field: 'id'",
                )
            if todo_id not in todo_map:
                raise build_error(
                    StatusCode.TOOL_TODOS_VALIDATION_INVALID,
                    reason=f"Batch update failed: Task with ID '{todo_id}' not found",
                )

            status_value = todo_data.get("status")
            if status_value in ("deleted", "delete"):
                deleted_ids.add(todo_id)
                continue

            current_todo = todo_map[todo_id]
            if "content" in todo_data:
                current_todo.content = todo_data["content"]
            if "activeForm" in todo_data:
                current_todo.activeForm = todo_data["activeForm"]
            if "description" in todo_data:
                current_todo.description = todo_data["description"]
            if "status" in todo_data:
                if status_value == "canceled":
                    status_value = "cancelled"
                current_todo.status = TodoStatus(status_value)
            if "selected_model_id" in todo_data:
                current_todo.selected_model_id = todo_data["selected_model_id"]
            updated_count += 1

        updated_todos = [todo for todo in current_todos if todo.id not in deleted_ids]
        self._validate_single_in_progress(updated_todos)
        await self.save_todos(session_id, updated_todos)

        parts: list[str] = []
        if updated_count:
            parts.append(f"Successfully updated {updated_count} task(s)")
        if deleted_ids:
            parts.append(
                f"Successfully deleted {len(deleted_ids)} task(s) (IDs: {', '.join(sorted(deleted_ids))})"
            )
        return "; ".join(parts) or "No task changes applied"


def install_todo_modify_compat_patch() -> None:
    """Patch OpenJiuWen exports so TaskPlanningRail uses the compatible tool."""
    import openjiuwen.harness.tools as tools_module
    import openjiuwen.harness.tools.todo as todo_module

    tools_module.TodoModifyTool = CompatibleTodoModifyTool
    todo_module.TodoModifyTool = CompatibleTodoModifyTool
