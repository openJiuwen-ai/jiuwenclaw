# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for TodoToolkit in jiuwenclaw.agentserver.tools.todo_toolkits."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub openjiuwen before loading todo_toolkits.
#
# openjiuwen is not installed in all environments. todo_toolkits.py itself
# imports ``from openjiuwen.core.foundation.tool import LocalFunction, Tool,
# ToolCard``. We provide functional stubs for those classes so get_tools()
# works end-to-end. The remaining openjiuwen submodules are MagicMock'd.
# ---------------------------------------------------------------------------

_OJ_PACKAGES = [
    "openjiuwen",
    "openjiuwen.core",
    "openjiuwen.core.foundation",
    "openjiuwen.core.session",
    "openjiuwen.extensions",
    "openjiuwen.extensions.context_evolver",
    "openjiuwen.extensions.context_evolver.core",
    "openjiuwen.extensions.context_evolver.core.file_connector",
    "openjiuwen.extensions.context_evolver.service",
]

for _pkg in _OJ_PACKAGES:
    if _pkg not in sys.modules:
        _mock = MagicMock()
        _mock.__path__ = []
        sys.modules[_pkg] = _mock


# Stub pydantic if not installed — TodoTask inherits BaseModel but tests
# only exercise toolkit logic, not model validation.
if "pydantic" not in sys.modules:
    _pydantic_mod = types.ModuleType("pydantic")

    class _BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    _pydantic_mod.BaseModel = _BaseModel
    sys.modules["pydantic"] = _pydantic_mod


# Functional stub for openjiuwen.core.foundation.tool
_tool_mod = types.ModuleType("openjiuwen.core.foundation.tool")
_tool_mod.__path__ = []


class _ToolCard:
    def __init__(self, name: str = "", description: str = "", input_params: dict | None = None):
        self.name = name
        self.description = description
        self.input_params = input_params or {}


class _LocalFunction:
    def __init__(self, card=None, func=None):
        self.card = card
        self.func = func


class _Tool:
    pass


_tool_mod.ToolCard = _ToolCard
_tool_mod.LocalFunction = _LocalFunction
_tool_mod.Tool = _Tool
_tool_mod.tool = lambda f: f
sys.modules["openjiuwen.core.foundation.tool"] = _tool_mod

# Stub openjiuwen.core.foundation.tool.tool (memory_tools.py uses it)
sys.modules.setdefault(
    "openjiuwen.core.foundation.tool.tool",
    types.ModuleType("openjiuwen.core.foundation.tool.tool"),
)
sys.modules["openjiuwen.core.foundation.tool.tool"].tool = lambda f: f

# ---------------------------------------------------------------------------
# Load todo_toolkits directly via importlib, bypassing tools/__init__.py
# which triggers heavy imports (jieba, memory, …) not needed here.
# ---------------------------------------------------------------------------

_TODO_TOOLKITS_PATH = (
    Path(__file__).resolve().parents[3]  # repo root
    / "jiuwenclaw" / "agentserver" / "tools" / "todo_toolkits.py"
)

_spec = importlib.util.spec_from_file_location(
    "todo_toolkits_under_test",
    _TODO_TOOLKITS_PATH,
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules["todo_toolkits_under_test"] = _mod  # needed by @dataclass
_spec.loader.exec_module(_mod)

TaskStatus = _mod.TaskStatus
TodoOpKind = _mod.TodoOpKind
TodoOpResult = _mod.TodoOpResult
TodoTask = _mod.TodoTask
TodoToolkit = _mod.TodoToolkit
consume_last_op_result = _mod.consume_last_op_result
reset_op_results = _mod.reset_op_results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_op_bus() -> None:
    """Clear the process-global op-result bus before each test."""
    reset_op_results()


@pytest.fixture
def toolkit(tmp_path: Path) -> TodoToolkit:
    """A TodoToolkit scoped to a temp directory."""
    tk = TodoToolkit(session_id="test-session", todo_dir=tmp_path)
    assert tk.todo_dir.exists()
    return tk


def _load(toolkit: TodoToolkit) -> list[TodoTask]:
    return toolkit.load_tasks()


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------

class TestPersistence:
    """Save → load round-trip preserves all fields."""

    def test_save_and_load_waiting(self, toolkit: TodoToolkit) -> None:
        tasks = [TodoTask(idx=1, tasks="write code", status=TaskStatus.WAITING)]
        toolkit._save_tasks(tasks)
        loaded = _load(toolkit)
        assert len(loaded) == 1
        assert loaded[0].idx == 1
        assert loaded[0].tasks == "write code"
        assert loaded[0].status == TaskStatus.WAITING
        assert loaded[0].result == ""

    def test_save_and_load_completed_with_result(self, toolkit: TodoToolkit) -> None:
        tasks = [
            TodoTask(idx=2, tasks="done thing", status=TaskStatus.COMPLETED, result="great"),
        ]
        toolkit._save_tasks(tasks)
        loaded = _load(toolkit)
        assert loaded[0].idx == 2
        assert loaded[0].status == TaskStatus.COMPLETED
        assert loaded[0].result == "great"

    def test_save_and_load_running(self, toolkit: TodoToolkit) -> None:
        toolkit._save_tasks([TodoTask(idx=1, tasks="run", status=TaskStatus.RUNNING)])
        loaded = _load(toolkit)
        assert loaded[0].status == TaskStatus.RUNNING

    def test_save_and_load_cancelled(self, toolkit: TodoToolkit) -> None:
        toolkit._save_tasks([TodoTask(idx=1, tasks="skip", status=TaskStatus.CANCELLED)])
        loaded = _load(toolkit)
        assert loaded[0].status == TaskStatus.CANCELLED

    def test_load_empty_returns_empty(self, toolkit: TodoToolkit) -> None:
        assert _load(toolkit) == []

    def test_load_sorted_by_idx(self, toolkit: TodoToolkit) -> None:
        tasks = [
            TodoTask(idx=3, tasks="c", status=TaskStatus.WAITING),
            TodoTask(idx=1, tasks="a", status=TaskStatus.WAITING),
            TodoTask(idx=2, tasks="b", status=TaskStatus.WAITING),
        ]
        toolkit._save_tasks(tasks)
        loaded = _load(toolkit)
        assert [t.idx for t in loaded] == [1, 2, 3]
        assert [t.tasks for t in loaded] == ["a", "b", "c"]

    def test_load_ignores_comments_and_blanks(self, toolkit: TodoToolkit) -> None:
        path = toolkit._todo_path
        path.write_text(
            "# Todo List\n"
            "\n"
            "- [ ] 1. real task | waiting\n"
            "\n"
            "# a comment\n"
            "- [x] 2. done | completed | ok\n",
            encoding="utf-8",
        )
        loaded = _load(toolkit)
        assert len(loaded) == 2
        assert loaded[0].tasks == "real task"
        assert loaded[1].tasks == "done"


# ---------------------------------------------------------------------------
# todo_create
# ---------------------------------------------------------------------------

class TestTodoCreate:

    def test_create_success(self, toolkit: TodoToolkit) -> None:
        result = toolkit.todo_create(["task A", "task B"])
        assert "Created 2 todo tasks" in result
        tasks = _load(toolkit)
        assert len(tasks) == 2
        assert tasks[0].idx == 1 and tasks[0].tasks == "task A"
        assert tasks[1].idx == 2 and tasks[1].tasks == "task B"
        assert all(t.status == TaskStatus.WAITING for t in tasks)

    def test_create_publishes_op_result(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["x"])
        op = consume_last_op_result(toolkit.session_id)
        assert op is not None
        assert op.kind == TodoOpKind.CREATE
        assert op.success is True
        assert op.total_count == 1
        assert op.remaining_count == 1
        assert op.all_completed is False

    def test_create_all_completed_when_single_done(self, toolkit: TodoToolkit) -> None:
        # Create then complete → op bus on complete should report all_completed
        toolkit.todo_create(["only"])
        toolkit.todo_complete(1)
        op = consume_last_op_result(toolkit.session_id)
        assert op.kind == TodoOpKind.COMPLETE
        assert op.all_completed is True
        assert op.remaining_count == 0

    def test_create_fails_if_already_exists(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["first"])
        result = toolkit.todo_create(["second"])
        assert "already exists" in result
        tasks = _load(toolkit)
        assert len(tasks) == 1
        assert tasks[0].tasks == "first"

    def test_create_duplicate_publishes_failure(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["first"])
        consume_last_op_result(toolkit.session_id)  # drain
        toolkit.todo_create(["second"])
        op = consume_last_op_result(toolkit.session_id)
        assert op.success is False
        assert op.kind == TodoOpKind.CREATE


# ---------------------------------------------------------------------------
# todo_start
# ---------------------------------------------------------------------------

class TestTodoStart:

    def test_start_success(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b"])
        result = toolkit.todo_start(1)
        assert "running" in result.lower()
        tasks = _load(toolkit)
        assert tasks[0].status == TaskStatus.RUNNING
        assert tasks[1].status == TaskStatus.WAITING

    def test_start_not_found(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        result = toolkit.todo_start(99)
        assert "not found" in result
        tasks = _load(toolkit)
        assert tasks[0].status == TaskStatus.WAITING

    def test_start_already_completed(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        toolkit.todo_complete(1)
        result = toolkit.todo_start(1)
        assert "already completed" in result

    def test_start_cancelled_task(self, toolkit: TodoToolkit) -> None:
        toolkit._save_tasks([
            TodoTask(idx=1, tasks="x", status=TaskStatus.CANCELLED),
        ])
        result = toolkit.todo_start(1)
        assert "cancelled" in result

    def test_start_publishes_op(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        toolkit.todo_start(1)
        op = consume_last_op_result(toolkit.session_id)
        assert op.kind == TodoOpKind.START
        assert op.success is True


# ---------------------------------------------------------------------------
# todo_complete
# ---------------------------------------------------------------------------

class TestTodoComplete:

    def test_complete_success(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        result = toolkit.todo_complete(1, result="finished")
        assert "completed" in result.lower()
        tasks = _load(toolkit)
        assert tasks[0].status == TaskStatus.COMPLETED
        assert tasks[0].result == "finished"

    def test_complete_default_result(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        toolkit.todo_complete(1)
        tasks = _load(toolkit)
        assert tasks[0].result == "done"

    def test_complete_not_found(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        result = toolkit.todo_complete(99)
        assert "not found" in result

    def test_complete_empty_result_becomes_done(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        toolkit.todo_complete(1, result="")
        assert _load(toolkit)[0].result == "done"


# ---------------------------------------------------------------------------
# todo_complete_batch
# ---------------------------------------------------------------------------

class TestTodoCompleteBatch:

    def test_batch_success(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b", "c"])
        result = toolkit.todo_complete_batch([1, 2])
        assert "completed" in result.lower()
        tasks = _load(toolkit)
        assert tasks[0].status == TaskStatus.COMPLETED
        assert tasks[1].status == TaskStatus.COMPLETED
        assert tasks[2].status == TaskStatus.WAITING

    def test_batch_with_results(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b"])
        toolkit.todo_complete_batch([1, 2], results=["r1", "r2"])
        tasks = _load(toolkit)
        assert tasks[0].result == "r1"
        assert tasks[1].result == "r2"

    def test_batch_default_results(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b"])
        toolkit.todo_complete_batch([1, 2])
        tasks = _load(toolkit)
        assert tasks[0].result == "done"
        assert tasks[1].result == "done"

    def test_batch_empty_indices(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        result = toolkit.todo_complete_batch([])
        assert "non-empty" in result
        assert _load(toolkit)[0].status == TaskStatus.WAITING

    def test_batch_mismatched_results_length(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b"])
        result = toolkit.todo_complete_batch([1, 2], results=["only_one"])
        assert "does not match" in result
        # Nothing written
        assert all(t.status == TaskStatus.WAITING for t in _load(toolkit))

    def test_batch_non_contiguous(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b", "c"])
        result = toolkit.todo_complete_batch([1, 3])
        assert "contiguous" in result
        assert all(t.status == TaskStatus.WAITING for t in _load(toolkit))

    def test_batch_not_starting_at_first_open(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b"])
        toolkit.todo_complete(1)
        consume_last_op_result(toolkit.session_id)
        # First open is now 2; trying to start batch at 1 (already done) → fail
        result = toolkit.todo_complete_batch([2])
        # This is valid — 2 is the first open
        assert "completed" in result.lower()

    def test_batch_wrong_start(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b", "c"])
        result = toolkit.todo_complete_batch([2, 3])
        assert "first open task" in result

    def test_batch_no_open_tasks(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        toolkit.todo_complete(1)
        consume_last_op_result(toolkit.session_id)
        result = toolkit.todo_complete_batch([1])
        assert "no open tasks" in result

    def test_batch_already_completed_target(self, toolkit: TodoToolkit) -> None:
        # Task 3 is already completed but 1 and 2 are waiting.
        # Batch [1, 2, 3] starts at first open (1), passes contiguous check,
        # but the per-task validation catches task 3 as already completed.
        toolkit._save_tasks([
            TodoTask(idx=1, tasks="a", status=TaskStatus.WAITING),
            TodoTask(idx=2, tasks="b", status=TaskStatus.WAITING),
            TodoTask(idx=3, tasks="c", status=TaskStatus.COMPLETED, result="done"),
        ])
        result = toolkit.todo_complete_batch([1, 2, 3])
        assert "already completed" in result

    def test_batch_cancelled_target(self, toolkit: TodoToolkit) -> None:
        # Task 3 is cancelled but 1 and 2 are waiting.
        toolkit._save_tasks([
            TodoTask(idx=1, tasks="a", status=TaskStatus.WAITING),
            TodoTask(idx=2, tasks="b", status=TaskStatus.WAITING),
            TodoTask(idx=3, tasks="c", status=TaskStatus.CANCELLED),
        ])
        result = toolkit.todo_complete_batch([1, 2, 3])
        assert "cancelled" in result

    def test_batch_atomicity_no_partial_write(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b", "c"])
        # [1, 3] is non-contiguous → should fail and not write anything
        toolkit.todo_complete_batch([1, 3])
        tasks = _load(toolkit)
        assert all(t.status == TaskStatus.WAITING for t in tasks)


# ---------------------------------------------------------------------------
# todo_insert
# ---------------------------------------------------------------------------

class TestTodoInsert:

    def test_insert_into_existing(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "c"])
        result = toolkit.todo_insert(2, ["b"])
        assert "Inserted" in result
        tasks = _load(toolkit)
        assert len(tasks) == 3
        assert [t.tasks for t in tasks] == ["a", "b", "c"]
        assert [t.idx for t in tasks] == [1, 2, 3]

    def test_insert_multiple(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "d"])
        toolkit.todo_insert(2, ["b", "c"])
        tasks = _load(toolkit)
        assert [t.tasks for t in tasks] == ["a", "b", "c", "d"]
        assert [t.idx for t in tasks] == [1, 2, 3, 4]

    def test_insert_at_end(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        toolkit.todo_insert(2, ["b"])
        tasks = _load(toolkit)
        assert [t.tasks for t in tasks] == ["a", "b"]

    def test_insert_into_empty_creates(self, toolkit: TodoToolkit) -> None:
        result = toolkit.todo_insert(1, ["x", "y"])
        assert "Created" in result
        tasks = _load(toolkit)
        assert len(tasks) == 2
        assert [t.tasks for t in tasks] == ["x", "y"]
        # Insert-into-empty should publish CREATE, not INSERT
        op = consume_last_op_result(toolkit.session_id)
        assert op.kind == TodoOpKind.CREATE

    def test_insert_shifts_existing_at_idx(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b", "c"])
        toolkit.todo_insert(2, ["x"])
        tasks = _load(toolkit)
        # Original "b" and "c" should be shifted to 3 and 4
        assert tasks[1].tasks == "x"
        assert tasks[2].tasks == "b"
        assert tasks[3].tasks == "c"


# ---------------------------------------------------------------------------
# todo_remove
# ---------------------------------------------------------------------------

class TestTodoRemove:

    def test_remove_success(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b", "c"])
        result = toolkit.todo_remove(2)
        assert "Removed" in result
        tasks = _load(toolkit)
        assert len(tasks) == 2
        assert [t.tasks for t in tasks] == ["a", "c"]
        # Renumbered
        assert [t.idx for t in tasks] == [1, 2]

    def test_remove_first(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b"])
        toolkit.todo_remove(1)
        tasks = _load(toolkit)
        assert len(tasks) == 1
        assert tasks[0].tasks == "b"
        assert tasks[0].idx == 1

    def test_remove_not_found(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        result = toolkit.todo_remove(99)
        assert "not found" in result
        assert len(_load(toolkit)) == 1

    def test_remove_renumbers_correctly(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b", "c", "d"])
        toolkit.todo_remove(2)  # remove "b"
        tasks = _load(toolkit)
        assert [t.idx for t in tasks] == [1, 2, 3]
        assert [t.tasks for t in tasks] == ["a", "c", "d"]


# ---------------------------------------------------------------------------
# todo_list
# ---------------------------------------------------------------------------

class TestTodoList:

    def test_list_empty(self, toolkit: TodoToolkit) -> None:
        result = toolkit.todo_list()
        assert result == "No todo tasks."

    def test_list_with_tasks(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b"])
        result = toolkit.todo_list()
        assert "a" in result
        assert "b" in result
        assert "[ ]" in result

    def test_list_shows_completed_icon(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        toolkit.todo_complete(1)
        result = toolkit.todo_list()
        assert "[x]" in result

    def test_list_shows_running_icon(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        toolkit.todo_start(1)
        result = toolkit.todo_list()
        assert "[>]" in result

    def test_list_shows_result_suffix(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        toolkit.todo_complete(1, result="success!")
        result = toolkit.todo_list()
        assert "success!" in result


# ---------------------------------------------------------------------------
# clear_tasks
# ---------------------------------------------------------------------------

class TestClearTasks:

    def test_clear_removes_file(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        assert toolkit._todo_path.exists()
        result = toolkit.clear_tasks()
        assert result is True
        assert not toolkit._todo_path.exists()

    def test_clear_when_no_file(self, toolkit: TodoToolkit) -> None:
        result = toolkit.clear_tasks()
        assert result is False

    def test_clear_then_recreate(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        toolkit.clear_tasks()
        # After clear, create should succeed (not "already exists")
        result = toolkit.todo_create(["b"])
        assert "Created" in result


# ---------------------------------------------------------------------------
# resolve_todo_path & set_session_id
# ---------------------------------------------------------------------------

class TestSessionId:

    def test_resolve_todo_path(self, toolkit: TodoToolkit) -> None:
        sid, path = toolkit.resolve_todo_path()
        assert sid == "test-session"
        assert path.name == "todo.md"

    def test_set_session_id_changes_path(self, tmp_path: Path) -> None:
        tk = TodoToolkit(session_id="s1", todo_dir=tmp_path)
        assert tk.session_id == "s1"
        tk.set_session_id("s2")
        assert tk.session_id == "s2"

    def test_explicit_session_id_used(self, tmp_path: Path) -> None:
        tk = TodoToolkit(session_id="my-session", todo_dir=tmp_path)
        assert tk.session_id == "my-session"


# ---------------------------------------------------------------------------
# Op-result publish / consume bus
# ---------------------------------------------------------------------------

class TestOpResultBus:

    def test_consume_is_one_shot(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        first = consume_last_op_result(toolkit.session_id)
        second = consume_last_op_result(toolkit.session_id)
        assert first is not None
        assert second is None

    def test_consume_empty_session(self) -> None:
        assert consume_last_op_result("nonexistent") is None

    def test_consume_empty_session_id(self) -> None:
        assert consume_last_op_result("") is None

    def test_op_result_fields_on_success(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a", "b"])
        op = consume_last_op_result(toolkit.session_id)
        assert op.kind == TodoOpKind.CREATE
        assert op.success is True
        assert op.remaining_count == 2
        assert op.total_count == 2
        assert op.all_completed is False

    def test_op_result_all_completed_true(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["only"])
        toolkit.todo_complete(1)
        op = consume_last_op_result(toolkit.session_id)
        assert op.all_completed is True
        assert op.remaining_count == 0
        assert op.total_count == 1

    def test_op_result_failure_not_all_completed(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        consume_last_op_result(toolkit.session_id)
        toolkit.todo_create(["b"])  # fails
        op = consume_last_op_result(toolkit.session_id)
        assert op.success is False
        assert op.all_completed is False

    def test_cancelled_excluded_from_counts(self, toolkit: TodoToolkit) -> None:
        toolkit._save_tasks([
            TodoTask(idx=1, tasks="a", status=TaskStatus.CANCELLED),
            TodoTask(idx=2, tasks="b", status=TaskStatus.WAITING),
        ])
        toolkit.todo_complete(2)
        op = consume_last_op_result(toolkit.session_id)
        # cancelled excluded from both remaining and total
        assert op.remaining_count == 0
        assert op.total_count == 1  # only completed (b), not cancelled (a)

    def test_reset_clears_all(self, toolkit: TodoToolkit) -> None:
        toolkit.todo_create(["a"])
        reset_op_results()
        assert consume_last_op_result(toolkit.session_id) is None


# ---------------------------------------------------------------------------
# get_tools
# ---------------------------------------------------------------------------

class TestGetTools:

    def test_returns_local_functions(self, toolkit: TodoToolkit) -> None:
        tools = toolkit.get_tools()
        assert len(tools) >= 5
        for tool in tools:
            # Each tool should have a card and a callable func
            assert hasattr(tool, "card")
            assert hasattr(tool, "func")
            assert callable(tool.func)

    def test_tool_names(self, toolkit: TodoToolkit) -> None:
        tools = toolkit.get_tools()
        names = [t.card.name for t in tools]
        assert "todo_create" in names
        assert "todo_complete" in names
        assert "todo_insert" in names
        assert "todo_remove" in names
        assert "todo_list" in names

    def test_start_exposed_by_default(self, toolkit: TodoToolkit) -> None:
        tools = toolkit.get_tools()
        names = [t.card.name for t in tools]
        assert "todo_start" in names

    def test_complete_batch_hidden_by_default(self, toolkit: TodoToolkit) -> None:
        tools = toolkit.get_tools()
        names = [t.card.name for t in tools]
        assert "todo_complete_batch" not in names

    def test_complete_batch_exposed_when_flag_set(self, tmp_path: Path) -> None:
        class BatchToolkit(TodoToolkit):
            EXPOSE_COMPLETE_BATCH = True

        tk = BatchToolkit(session_id="test", todo_dir=tmp_path)
        tools = tk.get_tools()
        names = [t.card.name for t in tools]
        assert "todo_complete_batch" in names

    def test_start_hidden_when_flag_unset(self, tmp_path: Path) -> None:
        class NoStartToolkit(TodoToolkit):
            EXPOSE_START = False

        tk = NoStartToolkit(session_id="test", todo_dir=tmp_path)
        tools = tk.get_tools()
        names = [t.card.name for t in tools]
        assert "todo_start" not in names
        assert "todo_create" in names

    def test_tool_function_callable(self, toolkit: TodoToolkit) -> None:
        tools = toolkit.get_tools()
        create_tool = next(t for t in tools if t.card.name == "todo_create")
        result = create_tool.func(["hello"])
        assert "Created" in result

    def test_tool_prefix_override(self, tmp_path: Path) -> None:
        class CustomPrefixToolkit(TodoToolkit):
            TOOL_PREFIX = "plan"

        tk = CustomPrefixToolkit(session_id="test", todo_dir=tmp_path)
        tools = tk.get_tools()
        names = [t.card.name for t in tools]
        assert "plan_create" in names
        assert "plan_list" in names
        assert "todo_create" not in names


# ---------------------------------------------------------------------------
# _compute_counts
# ---------------------------------------------------------------------------

class TestComputeCounts:

    def test_all_waiting(self) -> None:
        tasks = [
            TodoTask(idx=1, tasks="a", status=TaskStatus.WAITING),
            TodoTask(idx=2, tasks="b", status=TaskStatus.WAITING),
        ]
        remaining, total = TodoToolkit._compute_counts(tasks)
        assert remaining == 2
        assert total == 2

    def test_mixed_statuses(self) -> None:
        tasks = [
            TodoTask(idx=1, tasks="a", status=TaskStatus.WAITING),
            TodoTask(idx=2, tasks="b", status=TaskStatus.RUNNING),
            TodoTask(idx=3, tasks="c", status=TaskStatus.COMPLETED),
        ]
        remaining, total = TodoToolkit._compute_counts(tasks)
        assert remaining == 2  # waiting + running
        assert total == 3  # waiting + running + completed

    def test_cancelled_excluded(self) -> None:
        tasks = [
            TodoTask(idx=1, tasks="a", status=TaskStatus.CANCELLED),
            TodoTask(idx=2, tasks="b", status=TaskStatus.WAITING),
            TodoTask(idx=3, tasks="c", status=TaskStatus.COMPLETED),
        ]
        remaining, total = TodoToolkit._compute_counts(tasks)
        assert remaining == 1  # only waiting
        assert total == 2  # waiting + completed, cancelled excluded

    def test_empty(self) -> None:
        remaining, total = TodoToolkit._compute_counts([])
        assert remaining == 0
        assert total == 0
