# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Concurrency + fail-closed regression tests for skill_complete auto-flush.

Covers the P1 review findings:
  * auto-flush must FAIL CLOSED (fall back to _apply_skill_complete_block)
    when persistence did not fully land (missing file / stale id map),
    when ctx.session is None, or when the shared session lock is
    unavailable — instead of letting skill_complete through on an
    unverified state.
  * the read-modify-write must share the td tools' session lock so a
    concurrent todo_modify cannot interleave a stale-snapshot save that
    reverts the flush (lost update). CompatibleTodoModifyTool wraps its
    whole invoke (load+modify+save) in one lock acquisition so its RMW is
    atomic end-to-end w.r.t. auto-flush.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)
from openjiuwen.harness.tools.todo import TodoLockManager

from jiuwenswarm.agents.harness.common.rails.task_execution_rail import (
    TaskExecutionRail,
)
from jiuwenswarm.agents.harness.common.tools.todo_compat import (
    CompatibleTodoModifyTool,
)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, sid: str = "sess-1") -> None:
        self._sid = sid
        self.events: list[Any] = []

    def get_session_id(self) -> str:
        return self._sid

    async def write_stream(self, schema: Any) -> None:
        self.events.append(schema)


class _FakeFsResult:
    def __init__(self, code: int = 0, content: str = "") -> None:
        self.code = code
        self.data = SimpleNamespace(content=content)


class _FakeFs:
    """Filesystem that delegates to stdlib on the same path the tool resolves,
    so the rail (stdlib) and CompatibleTodoModifyTool (self.fs) touch the same
    on-disk td.json."""

    async def read_file(self, abs_path: str, mode: str = "text") -> _FakeFsResult:
        with open(abs_path, "r", encoding="utf-8") as f:
            return _FakeFsResult(0, f.read())

    async def write_file(
        self, abs_path: str, content: str, mode: str = "text"
    ) -> _FakeFsResult:
        os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        return _FakeFsResult(0)


class _FakeOperation:
    def fs(self) -> _FakeFs:
        return _FakeFs()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _make_ctx(
    *, tool_name: str = "skill_complete", sid: str = "sess-1"
) -> AgentCallbackContext:
    inputs = ToolCallInputs(
        tool_name=tool_name,
        tool_call=SimpleNamespace(id="tc-1"),
    )
    return AgentCallbackContext(
        agent=SimpleNamespace(),
        session=_FakeSession(sid),
        inputs=inputs,
    )


def _todo_path(tmp_path: Path, sid: str = "sess-1") -> Path:
    return tmp_path / sid / "todo.json"


def _wire_path(rail: TaskExecutionRail, tmp_path: Path) -> None:
    """Point the rail at the temp workspace. _deep_agent has no
    ability_manager, so the shared-lock resolver returns None by default
    (use _wire_lock to inject one)."""
    rail._deep_agent = SimpleNamespace()

    def _get_todo_workspace_path(_sid: str) -> Path:  # noqa: ARG001
        return _todo_path(tmp_path, _sid)

    rail._get_todo_workspace_path = _get_todo_workspace_path  # type: ignore[assignment]


def _wire_lock(rail: TaskExecutionRail, lock_manager: Any) -> None:
    """Inject a shared lock manager via the registered-tool resolver."""
    rail._resolve_todo_modify_tool = lambda: SimpleNamespace(  # type: ignore[assignment]
        _lock_manager=lock_manager
    )


def _write_todo(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _todo_map(items: list[dict]) -> dict[str, dict[str, Any]]:
    return {
        it["id"]: {
            "content": it.get("content", ""),
            "status": it.get("status", "pending"),
            "index": i,
            "total": len(items),
        }
        for i, it in enumerate(items)
    }


def _initial_items() -> list[dict]:
    return [
        {"id": "t1", "content": "T1", "status": "in_progress"},
        {"id": "t2", "content": "T2", "status": "pending"},
    ]


# --------------------------------------------------------------------------
# Fail-closed (P1 #1)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flush_fails_closed_when_session_is_none() -> None:
    """Incomplete todos + ctx.session is None → cannot persist/verify → block."""
    rail = TaskExecutionRail()
    rail._todo_map = _todo_map(_initial_items())

    inputs = ToolCallInputs(
        tool_name="skill_complete",
        tool_call=SimpleNamespace(id="tc-1"),
    )
    ctx = AgentCallbackContext(
        agent=SimpleNamespace(),
        session=None,
        inputs=inputs,
    )
    await rail.before_tool_call(ctx)

    assert ctx.extra.get("_skip_tool") is True
    assert "SKILL_COMPLETE_BLOCKED" in str(ctx.inputs.tool_result)


@pytest.mark.asyncio
async def test_flush_fails_closed_when_shared_lock_unavailable(tmp_path: Path) -> None:
    """Shared lock unreachable → no mutual exclusion → fail closed to block."""
    rail = TaskExecutionRail()
    _wire_path(rail, tmp_path)
    # No _wire_lock → _resolve_todo_modify_tool returns None.
    rail._todo_map = _todo_map(_initial_items())

    ctx = _make_ctx()
    await rail.before_tool_call(ctx)

    assert ctx.extra.get("_skip_tool") is True


@pytest.mark.asyncio
async def test_flush_fails_closed_when_todo_file_missing(tmp_path: Path) -> None:
    """Missing task-list file → persist raises → before_tool_call applies the block."""
    rail = TaskExecutionRail()
    _wire_path(rail, tmp_path)
    _wire_lock(rail, TodoLockManager())
    rail._todo_map = _todo_map(_initial_items())

    ctx = _make_ctx()
    await rail.before_tool_call(ctx)

    assert ctx.extra.get("_skip_tool") is True


@pytest.mark.asyncio
async def test_flush_fails_closed_on_stale_id_map(tmp_path: Path) -> None:
    """Override id not present in the task-list file → not all overrides landed → block."""
    rail = TaskExecutionRail()
    _wire_path(rail, tmp_path)
    _wire_lock(rail, TodoLockManager())
    _write_todo(
        _todo_path(tmp_path),
        [{"id": "t1", "content": "T1", "status": "completed"}],
    )
    rail._todo_map = _todo_map(
        [{"id": "ghost", "content": "Ghost", "status": "pending"}]
    )

    ctx = _make_ctx()
    await rail.before_tool_call(ctx)

    assert ctx.extra.get("_skip_tool") is True


@pytest.mark.asyncio
async def test_flush_fails_closed_when_concurrent_writer_re_adds_pending(
    tmp_path: Path,
) -> None:
    """A concurrent writer that re-adds a pending item between persist and the
    post-write verify is caught → block (fail closed)."""
    rail = TaskExecutionRail()
    _wire_path(rail, tmp_path)
    _wire_lock(rail, TodoLockManager())
    path = _todo_path(tmp_path)
    _write_todo(path, _initial_items())
    rail._todo_map = _todo_map(_initial_items())

    orig_persist = rail._persist_todo_statuses

    def _tampering_persist(sid: str, overrides: dict[str, str]) -> int:
        applied = orig_persist(sid, overrides)
        # Concurrent todo_modify sneaked in and reverted t2 to pending.
        tampered = [
            {"id": "t1", "content": "T1", "status": "completed"},
            {"id": "t2", "content": "T2", "status": "pending"},
        ]
        path.write_text(
            json.dumps(tampered, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return applied

    rail._persist_todo_statuses = _tampering_persist  # type: ignore[assignment]

    ctx = _make_ctx()
    await rail.before_tool_call(ctx)

    assert ctx.extra.get("_skip_tool") is True


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flush_marks_all_completed_and_emits_events(
    tmp_path: Path,
) -> None:
    rail = TaskExecutionRail()
    _wire_path(rail, tmp_path)
    _wire_lock(rail, TodoLockManager())
    path = _todo_path(tmp_path)
    _write_todo(path, _initial_items())
    rail._todo_map = _todo_map(_initial_items())

    ctx = _make_ctx()
    await rail.before_tool_call(ctx)

    assert ctx.extra.get("_skip_tool") is None
    items = json.loads(path.read_text(encoding="utf-8"))
    assert all(it["status"] == "completed" for it in items)
    types = [getattr(ev, "type", None) for ev in ctx.session.events]
    assert "task.update" in types


# --------------------------------------------------------------------------
# Shared lock / no lost update (P1 #2)
# --------------------------------------------------------------------------


class _SpyLockManager:
    """Wraps a real asyncio.Lock so the rail and a simulated todo_modify share
    one lock, while recording whether their critical sections ever overlapped."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.holder: str | None = None
        self.overlap = False
        self.flush_entered = False

    @asynccontextmanager
    async def operation(self, _sid: str, *, who: str = "flush"):
        async with self._lock:
            if self.holder is not None:
                self.overlap = True
            self.holder = who
            if who == "flush":
                self.flush_entered = True
            await asyncio.sleep(0)  # yield so a concurrent waiter tries (and blocks)
            try:
                yield
            finally:
                self.holder = None


@pytest.mark.asyncio
async def test_flush_shares_session_lock_with_atomic_modify(tmp_path: Path) -> None:
    """Auto-flush RMW and a (now-atomic) todo_modify RMW share the session lock;
    their critical sections never overlap and no completion is reverted."""
    rail = TaskExecutionRail()
    _wire_path(rail, tmp_path)
    path = _todo_path(tmp_path)
    _write_todo(path, _initial_items())
    rail._todo_map = _todo_map(_initial_items())

    spy = _SpyLockManager()
    _wire_lock(rail, spy)

    async def simulate_atomic_todo_modify() -> None:
        # Mirrors the fixed CompatibleTodoModifyTool: whole RMW under one lock.
        async with spy.operation("sess-1", who="modify"):
            assert spy.holder == "modify"
            data = json.loads(path.read_text(encoding="utf-8"))
            for it in data:
                if it["id"] == "t1":
                    it["status"] = "in_progress"
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    async def flush() -> None:
        await rail._flush_incomplete_todos_on_skill_complete(_make_ctx())

    tasks = [
        asyncio.create_task(flush()),
        *(asyncio.create_task(simulate_atomic_todo_modify()) for _ in range(4)),
    ]
    await asyncio.gather(*tasks)

    assert spy.flush_entered is True
    assert spy.overlap is False
    final = {
        it["id"]: it["status"]
        for it in json.loads(path.read_text(encoding="utf-8"))
    }
    assert final["t2"] == "completed"  # not reverted by a stale-snapshot save


@pytest.mark.asyncio
async def test_compatible_todo_modify_invoke_is_atomic(tmp_path: Path) -> None:
    """The real CompatibleTodoModifyTool holds the shared session lock across its
    whole load+modify+save, so a concurrent skill_complete auto-flush cannot
    interleave (no lost update, no overlap)."""
    spy = _SpyLockManager()
    tool = CompatibleTodoModifyTool(
        operation=_FakeOperation(),
        workspace=str(tmp_path),
        language="cn",
        lock_manager=spy,
    )
    path = _todo_path(tmp_path)
    _write_todo(path, _initial_items())

    rail = TaskExecutionRail()

    def _get_todo_workspace_path(_sid: str) -> Path:  # noqa: ARG001
        return path

    rail._get_todo_workspace_path = _get_todo_workspace_path  # type: ignore[assignment]
    rail._todo_map = _todo_map(_initial_items())
    _wire_lock(rail, spy)

    async def todo_modify() -> None:
        await tool.invoke(
            {"action": "update", "todos": [{"id": "t1", "status": "in_progress"}]},
            session=_FakeSession("sess-1"),
        )

    async def flush() -> None:
        await rail._flush_incomplete_todos_on_skill_complete(_make_ctx())

    tasks = [
        asyncio.create_task(todo_modify()),
        asyncio.create_task(flush()),
        *(asyncio.create_task(todo_modify()) for _ in range(3)),
    ]
    await asyncio.gather(*tasks)

    # Lock was used and no two critical sections overlapped.
    assert spy.overlap is False
    # No lost update: t2's completion by the flush was not reverted.
    final = {
        it["id"]: it["status"]
        for it in json.loads(path.read_text(encoding="utf-8"))
    }
    assert final["t2"] == "completed"
