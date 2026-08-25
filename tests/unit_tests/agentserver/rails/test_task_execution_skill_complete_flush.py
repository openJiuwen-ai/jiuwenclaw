# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Concurrency + fail-closed regression tests for skill_complete auto-flush.

Covers the two P1 review findings:
  * auto-flush must FAIL CLOSED (fall back to _apply_skill_complete_block)
    when persistence did not fully land (missing file / stale id map) —
    instead of letting skill_complete through on an unverified state.
  * the read-modify-write must share the task tools' session lock so a
    concurrent todo_modify (parallel_tool_calls can emit both in one turn)
    cannot interleave a stale-snapshot write that reverts the flush
    (lost update that os.replace alone cannot prevent).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)

from jiuwenswarm.agents.harness.common.rails.task_execution_rail import (
    TaskExecutionRail,
)


class _FakeSession:
    def __init__(self, sid: str = "sess-1") -> None:
        self._sid = sid
        self.events: list[Any] = []

    def get_session_id(self) -> str:
        return self._sid

    async def write_stream(self, schema: Any) -> None:
        self.events.append(schema)


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
    """Point the rail at the temp workspace; no ability_manager so the
    shared-lock resolver returns None (unlocked-but-verified path)."""
    rail._deep_agent = SimpleNamespace()

    def _get_todo_workspace_path(_sid: str) -> Path:  # noqa: ARG001
        return _todo_path(tmp_path, _sid)

    rail._get_todo_workspace_path = _get_todo_workspace_path  # type: ignore[assignment]


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


# --------------------------------------------------------------------------
# Fail-closed (P1 #1)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flush_fails_closed_when_todo_file_missing(tmp_path: Path) -> None:
    """Missing task-list file → persist raises → before_tool_call applies the block."""
    rail = TaskExecutionRail()
    _wire_path(rail, tmp_path)
    rail._todo_map = _todo_map(
        [
            {"id": "t1", "content": "T1", "status": "in_progress"},
            {"id": "t2", "content": "T2", "status": "pending"},
        ]
    )

    ctx = _make_ctx()
    await rail.before_tool_call(ctx)

    assert ctx.extra.get("_skip_tool") is True
    assert "SKILL_COMPLETE_BLOCKED" in str(ctx.inputs.tool_result)


@pytest.mark.asyncio
async def test_flush_fails_closed_on_stale_id_map(tmp_path: Path) -> None:
    """Override id not present in the task-list file → not all overrides landed → block."""
    rail = TaskExecutionRail()
    _wire_path(rail, tmp_path)
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
    path = _todo_path(tmp_path)
    _write_todo(
        path,
        [
            {"id": "t1", "content": "T1", "status": "in_progress"},
            {"id": "t2", "content": "T2", "status": "pending"},
        ],
    )
    rail._todo_map = _todo_map(
        [
            {"id": "t1", "content": "T1", "status": "in_progress"},
            {"id": "t2", "content": "T2", "status": "pending"},
        ]
    )

    # No shared lock on this path (ability_manager absent) → unlocked persist +
    # verify. Tamper the file back to pending right after persist so the verify
    # reload sees an incomplete task and raises.
    orig_persist = rail._persist_todo_statuses

    def _tampering_persist(sid: str, overrides: dict[str, str]) -> int:
        applied = orig_persist(sid, overrides)
        # Concurrent todo_modify sneaked in and reverted t2 to pending.
        tampered = [
            {"id": "t1", "content": "T1", "status": "completed"},
            {"id": "t2", "content": "T2", "status": "pending"},
        ]
        path.write_text(
            json.dumps(tampered, ensure_ascii=False, indent=2), encoding="utf-8"
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
    path = _todo_path(tmp_path)
    _write_todo(
        path,
        [
            {"id": "t1", "content": "T1", "status": "in_progress"},
            {"id": "t2", "content": "T2", "status": "pending"},
        ],
    )
    rail._todo_map = _todo_map(
        [
            {"id": "t1", "content": "T1", "status": "in_progress"},
            {"id": "t2", "content": "T2", "status": "pending"},
        ]
    )

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
            # Yield so a concurrent waiter gets a chance to try (and block).
            await asyncio.sleep(0)
            try:
                yield
            finally:
                self.holder = None


@pytest.mark.asyncio
async def test_flush_shares_session_lock_with_todo_modify(tmp_path: Path) -> None:
    """The auto-flush RMW must acquire the registered todo_modify tool's shared
    session lock, so a concurrent todo_modify cannot interleave (lost update)."""
    rail = TaskExecutionRail()
    _wire_path(rail, tmp_path)
    path = _todo_path(tmp_path)
    _write_todo(
        path,
        [
            {"id": "t1", "content": "T1", "status": "in_progress"},
            {"id": "t2", "content": "T2", "status": "pending"},
        ],
    )
    rail._todo_map = _todo_map(
        [
            {"id": "t1", "content": "T1", "status": "in_progress"},
            {"id": "t2", "content": "T2", "status": "pending"},
        ]
    )

    spy = _SpyLockManager()
    modify_tool = SimpleNamespace(_lock_manager=spy)
    rail._resolve_todo_modify_tool = lambda: modify_tool  # type: ignore[assignment]

    async def simulate_concurrent_todo_modify() -> None:
        # A concurrent todo_modify RMW using the SAME shared lock manager.
        async with spy.operation("sess-1", who="modify"):
            assert spy.holder == "modify"
            data = json.loads(path.read_text(encoding="utf-8"))
            for it in data:
                if it["id"] == "t1":
                    it["status"] = "in_progress"
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    async def flush() -> None:
        await rail._flush_incomplete_todos_on_skill_complete(_make_ctx())

    # Run flush and several concurrent todo_modify writers at once.
    tasks = [
        asyncio.create_task(flush()),
        *(
            asyncio.create_task(simulate_concurrent_todo_modify())
            for _ in range(4)
        ),
    ]
    await asyncio.gather(*tasks)

    # The rail actually entered the shared lock (didn't silently skip it).
    assert spy.flush_entered is True
    # No two critical sections ever overlapped (true mutual exclusion).
    assert spy.overlap is False
    # No lost update: t2's completion by the flush was not reverted by any
    # concurrent todo_modify's stale-snapshot write.
    final = {it["id"]: it["status"] for it in json.loads(path.read_text(encoding="utf-8"))}
    assert final["t2"] == "completed"
