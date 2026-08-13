# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Todo toolkit for agent task tracking.

Provides todo_create, todo_complete, todo_insert, todo_remove tools that persist
tasks to a markdown file under the predefined session directory. Tools can be
registered in the openJiuwen Runner via TodoToolkit.get_tools().

TodoToolkit 可无参构造，此时 session_id 在每次工具调用时通过
jiuwenclaw.agentserver.plan_todo_context.get_plan_todo_session_id() 动态解析，
使一个全局注册的实例能在多 session 并发下正确路由到各自的
agent/sessions/{session_id}/ 文件。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, ClassVar, Dict, List, Optional

from pydantic import BaseModel

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenclaw.utils import get_agent_sessions_dir


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    WAITING = "waiting"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TodoTask(BaseModel):
    idx: int
    tasks: str
    status: TaskStatus
    result: str = ""


# ---------------------------------------------------------------------------
# Status ↔ checkbox mapping (shared by load / save / list)
# ---------------------------------------------------------------------------

_STATUS_CHECKBOX: Dict[TaskStatus, str] = {
    TaskStatus.COMPLETED: "[x]",
    TaskStatus.CANCELLED: "[-]",
    TaskStatus.RUNNING: "[>]",
    TaskStatus.WAITING: "[ ]",
}

_CHECKBOX_STATUS: Dict[str, TaskStatus] = {
    "[x]": TaskStatus.COMPLETED,
    "[√]": TaskStatus.COMPLETED,
    "[-]": TaskStatus.CANCELLED,
    "[>]": TaskStatus.RUNNING,
    "[ ]": TaskStatus.WAITING,
}

# Status names for parsing the ``| status`` field (lowercased lookup)
_STATUS_NAMES = {s.value: s for s in TaskStatus}


# ---------------------------------------------------------------------------
# Operation result (publish / consume bus)
# ---------------------------------------------------------------------------

class TodoOpKind(str, Enum):
    CREATE = "create"
    START = "start"
    COMPLETE = "complete"
    INSERT = "insert"
    REMOVE = "remove"


@dataclass(frozen=True)
class TodoOpResult:
    """Structured result of a write operation.

    ``cancelled`` tasks are excluded from both ``remaining_count`` and
    ``total_count`` (treated as voided, not as plan members).
    """

    kind: TodoOpKind
    success: bool
    message: str
    remaining_count: int      # waiting + running, after the op
    total_count: int          # waiting + running + completed, after the op
    all_completed: bool       # True iff total_count > 0 and remaining_count == 0


_last_op_result: Dict[str, TodoOpResult] = {}
_last_op_result_lock = threading.Lock()


def _publish_op_result(session_id: str, result: TodoOpResult) -> None:
    if not session_id:
        return
    with _last_op_result_lock:
        _last_op_result[session_id] = result


def consume_last_op_result(session_id: str) -> Optional[TodoOpResult]:
    """Pop the most recent TodoOpResult for ``session_id`` (one-shot)."""
    if not session_id:
        return None
    with _last_op_result_lock:
        return _last_op_result.pop(session_id, None)


def reset_op_results() -> None:
    """Clear the entire ``TodoOpResult`` publish-bus across all sessions.

    Intended for test isolation between cases that share the process-global
    publish bus. Production code should not need this — use
    :func:`consume_last_op_result` for normal one-shot consumption.
    """
    with _last_op_result_lock:
        _last_op_result.clear()


def _resolve_runtime_session_id() -> str:
    """从 plan_todo_context 解析当前请求 session_id，失败兜底 "default"。"""
    try:
        from jiuwenclaw.agentserver.plan_todo_context import get_plan_todo_session_id
        return get_plan_todo_session_id() or "default"
    except Exception:
        return "default"


# ---------------------------------------------------------------------------
# TodoToolkit
# ---------------------------------------------------------------------------

class TodoToolkit:
    """Toolkit for agent todo task tracking. Persists tasks to markdown under session dir."""

    TODO_FILENAME: ClassVar[str] = "todo.md"
    TOOL_PREFIX: ClassVar[str] = "todo"

    # Subclasses may flip these to tailor which surface is exposed without
    # rewriting get_tools.
    EXPOSE_START: ClassVar[bool] = True
    EXPOSE_COMPLETE_BATCH: ClassVar[bool] = False

    # Per {class}:{session_id} file locks — prevents concurrent
    # read-modify-write races on the same todo.md.
    _session_locks: ClassVar[Dict[str, threading.Lock]] = {}
    _meta_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def _get_session_lock(cls, session_id: str) -> threading.Lock:
        """获取指定 session 的文件操作锁（线程安全）."""
        key = f"{cls.__name__}:{session_id}"
        with cls._meta_lock:
            if key not in cls._session_locks:
                cls._session_locks[key] = threading.Lock()
            return cls._session_locks[key]

    def __init__(self, session_id: Optional[str] = None, todo_dir: Path | None = None):
        """Initialize TodoToolkit for a session.

        Args:
            session_id: Session/conversation identifier for scoping todo files. 为 None
                时每次工具调用从 plan_todo_context 动态解析，同一实例可被多 session 并发共用。
            todo_dir: Optional custom directory. Defaults to agent/sessions/{session_id}/.
        """
        self._explicit_session_id: Optional[str] = session_id
        self._fixed_todo_dir: Optional[Path] = Path(todo_dir) if todo_dir is not None else None

    @property
    def session_id(self) -> str:
        return self._explicit_session_id or _resolve_runtime_session_id()

    def set_session_id(self, session_id: str) -> None:
        """Set the session ID for this toolkit instance."""
        self._explicit_session_id = session_id

    @property
    def todo_dir(self) -> Path:
        if self._fixed_todo_dir is not None:
            todo_dir = self._fixed_todo_dir
        else:
            todo_dir = get_agent_sessions_dir() / self.session_id
        todo_dir.mkdir(parents=True, exist_ok=True)
        return todo_dir

    @property
    def _todo_path(self) -> Path:
        return self.todo_dir / self.__class__.TODO_FILENAME

    def resolve_todo_path(self) -> tuple[str, Path]:
        """Return (session_id, todo_file_path) for external callers (e.g. rails)."""
        return self.session_id, self._todo_path

    def load_tasks(self) -> List[TodoTask]:
        """Public wrapper of :meth:`_load_tasks` for external callers (e.g. rails)."""
        return self._load_tasks()

    def clear_tasks(self) -> bool:
        """Delete the todo file for the current session if it exists.

        Returns True if a file was actually removed, False if there was nothing
        to remove. Safe under the per-session lock.
        """
        with self._get_session_lock(self.session_id):
            path = self._todo_path
            if not path.exists():
                return False
            path.unlink()
            return True

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_tasks(self) -> List[TodoTask]:
        """Load tasks from markdown file."""
        path = self._todo_path
        if not path.exists():
            return []
        tasks: List[TodoTask] = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Determine status from checkbox marker
                status = TaskStatus.WAITING
                for cb, st in _CHECKBOX_STATUS.items():
                    if cb in line:
                        status = st
                        break
                # Split on "|" → [task_text, status, result?]
                parts = [p.strip() for p in line.split("|")]
                result = parts[2] if len(parts) >= 3 else ""
                # Status field (parts[1]) overrides checkbox if present
                if len(parts) >= 2:
                    status = _STATUS_NAMES.get(parts[1].lower(), status)
                # Strip checkbox prefix to get "idx. task_text"
                rest = line
                for cb in _CHECKBOX_STATUS:
                    rest = rest.replace(f"- {cb}", "")
                rest = rest.strip()
                if "." in rest:
                    idx_str, _, task_text = rest.partition(".")
                    task_text = task_text.split("|")[0].strip()
                    try:
                        idx = int(idx_str.strip())
                        tasks.append(
                            TodoTask(idx=idx, tasks=task_text, status=status, result=result)
                        )
                    except ValueError:
                        pass
        return sorted(tasks, key=lambda t: t.idx)

    def _save_tasks(self, tasks: List[TodoTask]) -> None:
        """Save tasks to markdown file."""
        lines = ["# Todo List", ""]
        for t in sorted(tasks, key=lambda x: x.idx):
            checkbox = _STATUS_CHECKBOX.get(t.status, "[ ]")
            line = f"- {checkbox} {t.idx}. {t.tasks} | {t.status.value}"
            if t.result:
                line += f" | {t.result}"
            lines.append(line)
        self.todo_dir.mkdir(parents=True, exist_ok=True)
        with open(self._todo_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    # ------------------------------------------------------------------
    # Publish / format helpers
    # ------------------------------------------------------------------

    def _append_todo_list(self, message: str) -> str:
        """Append current todo list to a status message."""
        return f"{message}\n\nCurrent todo list:\n{self.todo_list()}"

    @staticmethod
    def _compute_counts(tasks: List[TodoTask]) -> tuple[int, int]:
        """Return (remaining_count, total_count). cancelled excluded from both."""
        remaining = sum(
            1 for t in tasks if t.status in (TaskStatus.WAITING, TaskStatus.RUNNING)
        )
        completed = sum(1 for t in tasks if t.status == TaskStatus.COMPLETED)
        return remaining, remaining + completed

    def _publish(
        self, kind: TodoOpKind, success: bool, message: str,
        tasks_after: Optional[List[TodoTask]],
    ) -> None:
        """Compute counts from ``tasks_after`` and publish a TodoOpResult."""
        if tasks_after is None:
            remaining, total = 0, 0
        else:
            remaining, total = self._compute_counts(tasks_after)
        _publish_op_result(self.session_id, TodoOpResult(
            kind=kind,
            success=success,
            message=message,
            remaining_count=remaining,
            total_count=total,
            all_completed=(success and total > 0 and remaining == 0),
        ))

    def _fail(self, kind: TodoOpKind, msg: str, tasks: List[TodoTask]) -> str:
        """Publish failure and return the appended message."""
        self._publish(kind, False, msg, tasks)
        return self._append_todo_list(msg)

    def _ok(self, kind: TodoOpKind, msg: str, tasks: List[TodoTask]) -> str:
        """Publish success and return the appended message."""
        self._publish(kind, True, msg, tasks)
        return self._append_todo_list(msg)

    # ------------------------------------------------------------------
    # Public todo API
    # ------------------------------------------------------------------

    def todo_create(self, tasks: List[str]) -> str:
        """Create a list of todo tasks. Fails if a todo list already exists.

        Args:
            tasks: List of task descriptions to create.

        Returns:
            Status message (success or error) and current todo list.
        """
        with self._get_session_lock(self.session_id):
            if self._todo_path.exists():
                msg = (
                    f"Error: A todo list for session {self.session_id} already exists. "
                    f"Use {self.__class__.TOOL_PREFIX}_insert to add more tasks."
                )
                return self._fail(TodoOpKind.CREATE, msg, self._load_tasks())
            todo_tasks = [
                TodoTask(idx=i + 1, tasks=t, status=TaskStatus.WAITING, result="")
                for i, t in enumerate(tasks)
            ]
            self._save_tasks(todo_tasks)
            return self._ok(TodoOpKind.CREATE, f"Created {len(todo_tasks)} todo tasks.", todo_tasks)

    def todo_start(self, idx: int) -> str:
        """Mark a task as running (in progress).

        Args:
            idx: 1-based index of the task.

        Returns:
            Status message and current todo list.
        """
        with self._get_session_lock(self.session_id):
            todo_tasks = self._load_tasks()
            for t in todo_tasks:
                if t.idx == idx:
                    if t.status == TaskStatus.COMPLETED:
                        return self._fail(TodoOpKind.START, f"Error: Task {idx} is already completed.", todo_tasks)
                    if t.status == TaskStatus.CANCELLED:
                        return self._fail(TodoOpKind.START, f"Error: Task {idx} is cancelled.", todo_tasks)
                    t.status = TaskStatus.RUNNING
                    self._save_tasks(todo_tasks)
                    return self._ok(TodoOpKind.START, f"Task {idx} marked as running.", todo_tasks)
            return self._fail(TodoOpKind.START, f"Error: Task {idx} not found.", todo_tasks)

    def todo_complete(self, idx: int, result: str = "") -> str:
        """Mark a task as completed and save a brief result.

        Args:
            idx: 1-based index of the task.
            result: Brief result or outcome of the task.

        Returns:
            Status message and current todo list.
        """
        with self._get_session_lock(self.session_id):
            todo_tasks = self._load_tasks()
            for t in todo_tasks:
                if t.idx == idx:
                    t.status = TaskStatus.COMPLETED
                    t.result = result or "done"
                    self._save_tasks(todo_tasks)
                    return self._ok(TodoOpKind.COMPLETE, f"Task {idx} marked as completed.", todo_tasks)
            return self._fail(TodoOpKind.COMPLETE, f"Error: Task {idx} not found.", todo_tasks)

    def todo_complete_batch(
        self, indices: List[int], results: Optional[List[str]] = None,
    ) -> str:
        """Mark several tasks as completed in a single call.

        Indices must be strictly ascending and start at the first task that is
        not yet completed/cancelled, so the agent cannot retroactively close a
        gap or close steps out of order. ``results`` is optional; when provided
        it must align 1:1 with ``indices``. The whole batch is atomic — on any
        validation error nothing is written and a single failure event is
        published.
        """
        with self._get_session_lock(self.session_id):
            todo_tasks = self._load_tasks()

            if not indices:
                return self._fail(TodoOpKind.COMPLETE, "Error: indices must be a non-empty list.", todo_tasks)

            if results is None:
                results = ["done"] * len(indices)
            elif len(results) != len(indices):
                msg = f"Error: results length ({len(results)}) does not match indices length ({len(indices)})."
                return self._fail(TodoOpKind.COMPLETE, msg, todo_tasks)

            # Contiguous ascending check
            for i in range(1, len(indices)):
                if indices[i] != indices[i - 1] + 1:
                    return self._fail(
                        TodoOpKind.COMPLETE,
                        f"Error: indices must be strictly ascending and contiguous. Got {indices}.",
                        todo_tasks,
                    )

            # Must start at the first open task
            first_open = next(
                (t.idx for t in todo_tasks
                 if t.status in (TaskStatus.WAITING, TaskStatus.RUNNING)),
                None,
            )
            if first_open is None:
                return self._fail(TodoOpKind.COMPLETE, "Error: no open tasks left to complete.", todo_tasks)
            if indices[0] != first_open:
                return self._fail(
                    TodoOpKind.COMPLETE,
                    f"Error: batch must start at idx {first_open} (the first open task); got start idx {indices[0]}.",
                    todo_tasks,
                )

            # Validate each target task exists and is open
            tasks_by_idx = {t.idx: t for t in todo_tasks}
            for idx in indices:
                t = tasks_by_idx.get(idx)
                if t is None:
                    return self._fail(TodoOpKind.COMPLETE, f"Error: Task {idx} not found.", todo_tasks)
                if t.status == TaskStatus.COMPLETED:
                    return self._fail(TodoOpKind.COMPLETE, f"Error: Task {idx} is already completed.", todo_tasks)
                if t.status == TaskStatus.CANCELLED:
                    return self._fail(TodoOpKind.COMPLETE, f"Error: Task {idx} is cancelled.", todo_tasks)

            # Apply — all validations passed
            for idx, result in zip(indices, results):
                t = tasks_by_idx[idx]
                t.status = TaskStatus.COMPLETED
                t.result = (result or "").strip() or "done"

            self._save_tasks(todo_tasks)
            return self._ok(TodoOpKind.COMPLETE, f"Tasks {list(indices)} marked as completed.", todo_tasks)

    def todo_insert(self, idx: int, tasks: List[str]) -> str:
        """Insert new tasks at the given index. Existing tasks are shifted.

        Args:
            idx: 1-based index where to insert (tasks will start at this index).
            tasks: New task descriptions to insert.

        Returns:
            Status message and current todo list.
        """
        with self._get_session_lock(self.session_id):
            todo_tasks = self._load_tasks()
            if not self._todo_path.exists():
                # Lock held — create directly to avoid race after unlock
                new_tasks = [
                    TodoTask(idx=i + 1, tasks=t, status=TaskStatus.WAITING, result="")
                    for i, t in enumerate(tasks)
                ]
                self._save_tasks(new_tasks)
                msg = f"Created {len(new_tasks)} todo tasks."
                # Insert-into-empty is semantically a create — publish CREATE so
                # the task execution rail can observe the todo status transition.
                self._publish(TodoOpKind.CREATE, True, msg, new_tasks)
                return self._append_todo_list(msg)
            new_tasks = [
                TodoTask(idx=i + idx, tasks=t, status=TaskStatus.WAITING, result="")
                for i, t in enumerate(tasks)
            ]
            # Shift existing tasks at or after idx
            for t in todo_tasks:
                if t.idx >= idx:
                    t.idx += len(tasks)
            todo_tasks.extend(new_tasks)
            todo_tasks.sort(key=lambda x: x.idx)
            self._save_tasks(todo_tasks)
            return self._ok(TodoOpKind.INSERT, f"Inserted {len(tasks)} task(s) at index {idx}.", todo_tasks)

    def todo_remove(self, idx: int) -> str:
        """Remove a task and renumber remaining tasks.

        Args:
            idx: 1-based index of the task to remove.

        Returns:
            Status message and current todo list.
        """
        with self._get_session_lock(self.session_id):
            todo_tasks = self._load_tasks()
            if not any(t.idx == idx for t in todo_tasks):
                return self._fail(TodoOpKind.REMOVE, f"Error: Task {idx} not found.", todo_tasks)
            todo_tasks = [t for t in todo_tasks if t.idx != idx]
            # Renumber
            for i, t in enumerate(todo_tasks, 1):
                t.idx = i
            self._save_tasks(todo_tasks)
            return self._ok(TodoOpKind.REMOVE, f"Removed task {idx}.", todo_tasks)

    def todo_list(self) -> str:
        """List all current todo tasks.

        Returns:
            Formatted string of tasks.
        """
        todo_tasks = self._load_tasks()
        if not todo_tasks:
            return "No todo tasks."
        lines = []
        for t in todo_tasks:
            icon = _STATUS_CHECKBOX.get(t.status, "[ ]")
            suffix = f" | {t.result}" if t.result else ""
            lines.append(f"- {icon} {t.idx}. {t.tasks}{suffix}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def get_tools(self) -> List[Tool]:
        """Return all todo tools for registration in the openJiuwen Runner.

        Usage:
            toolkit = TodoToolkit(session_id="abc123")
            tools = toolkit.get_tools()
            Runner.resource_mgr.add_tool(tools)
            for t in tools:
                agent.ability_manager.add(t.card)

        Returns:
            List of Tool instances (LocalFunction) ready for Runner/agent registration.
        """
        prefix = self.__class__.TOOL_PREFIX
        expose_batch = self.__class__.EXPOSE_COMPLETE_BATCH

        complete_desc = "Mark a task as completed and save a brief result."
        if expose_batch:
            complete_desc = (
                "Mark a single task as completed and save a brief result. "
                f"When several already-finished steps can be closed together, "
                f"prefer ``{prefix}_complete_batch`` to avoid extra tool round-trips."
            )

        # Ordered tool specs: (name, description, params, func, always_expose)
        # Conditional tools (start, complete_batch) are filtered by the class flag.
        specs: List[tuple[str, str, dict, Callable, bool]] = [
            (
                f"{prefix}_create",
                "Create a list of todo tasks. Cannot be called when a todo list already exists. "
                "Use this to plan and track work. Pass a list of task descriptions.",
                {
                    "type": "object",
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of task descriptions to create",
                        }
                    },
                    "required": ["tasks"],
                },
                lambda tasks: self.todo_create(tasks),
                True,
            ),
            (
                f"{prefix}_start",
                "Mark a task as running (in progress). Call this before starting to work on a task.",
                {
                    "type": "object",
                    "properties": {
                        "idx": {"type": "integer", "description": "1-based index of the task to start"},
                    },
                    "required": ["idx"],
                },
                lambda idx: self.todo_start(idx),
                self.__class__.EXPOSE_START,
            ),
            (
                f"{prefix}_complete",
                complete_desc,
                {
                    "type": "object",
                    "properties": {
                        "idx": {"type": "integer", "description": "1-based index of the task to complete"},
                        "result": {"type": "string", "description": "Brief result or outcome", "default": ""},
                    },
                    "required": ["idx"],
                },
                lambda idx, result="": self.todo_complete(idx, result),
                True,
            ),
            (
                f"{prefix}_complete_batch",
                "Mark several already-finished tasks as completed in one call. "
                "Indices must be strictly ascending, contiguous, and start at "
                "the first open task — gaps, reordering, and closing "
                "already-completed tasks are rejected. Use this only for steps "
                "that are truly done; never pre-close steps.",
                {
                    "type": "object",
                    "properties": {
                        "indices": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": (
                                "1-based indices of tasks to complete; "
                                "strictly ascending and contiguous, must "
                                "start at the first open task."
                            ),
                        },
                        "results": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Per-task brief outcomes, aligned 1:1 with "
                                "indices. Optional; defaults to 'done' for "
                                "each entry."
                            ),
                        },
                    },
                    "required": ["indices"],
                },
                lambda indices, results=None: self.todo_complete_batch(indices, results),
                expose_batch,
            ),
            (
                f"{prefix}_insert",
                "Insert new tasks at the given index. Existing tasks are shifted.",
                {
                    "type": "object",
                    "properties": {
                        "idx": {"type": "integer", "description": "1-based index where to insert"},
                        "tasks": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "New task descriptions to insert",
                        },
                    },
                    "required": ["idx", "tasks"],
                },
                lambda idx, tasks: self.todo_insert(idx, tasks),
                True,
            ),
            (
                f"{prefix}_remove",
                "Remove a task by index. Remaining tasks are renumbered.",
                {
                    "type": "object",
                    "properties": {
                        "idx": {"type": "integer", "description": "1-based index of the task to remove"},
                    },
                    "required": ["idx"],
                },
                lambda idx: self.todo_remove(idx),
                True,
            ),
            (
                f"{prefix}_list",
                "List all current todo tasks with their status.",
                {"type": "object", "properties": {}},
                lambda: self.todo_list(),
                True,
            ),
        ]

        tools: List[Tool] = []
        for name, desc, params, func, expose in specs:
            if not expose:
                continue
            tools.append(LocalFunction(
                card=ToolCard(name=name, description=desc, input_params=params),
                func=func,
            ))
        return tools
