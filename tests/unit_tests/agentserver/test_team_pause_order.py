# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pause aligns with DEV: Runner.pause to completion, then wait for stream exit."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jiuwenclaw.agentserver.team import team_manager as tm_mod


class _OrderRecorder:
    def __init__(self) -> None:
        self.events: list[str] = []
        self._stream_tasks: dict[str, asyncio.Task] = {}
        self._team_monitors: dict[str, Any] = {"sess-1": object()}
        self._active_pause_tasks: dict[str, asyncio.Task | None] = {}
        self._cancel_requested: dict[str, bool] = {}
        self._lifecycle_locks: dict[str, asyncio.Lock] = {}

    def _get_lifecycle_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._lifecycle_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._lifecycle_locks[session_id] = lock
        return lock

    def _has_local_team_runtime(self, session_id: str) -> bool:
        return False

    def is_runtime_active(self, session_id: str) -> bool:
        return session_id == "sess-1"

    def is_runtime_pending(self, session_id: str) -> bool:
        return False

    def _resolve_session_team_name(self, session_id: str) -> str | None:
        return "team-demo" if session_id == "sess-1" else None

    async def _wait_for_stream_task_exit(
        self, session_id: str, *, timeout_sec: float = 0.05
    ) -> bool:
        self.events.append("wait_stream_exit")
        task = self._stream_tasks.get(session_id)
        if task is None or task.done():
            return True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_sec)
            return True
        except asyncio.TimeoutError:
            return False

    def _pop_and_cancel_stream_task_unlocked(
        self, session_id: str, reason: str
    ) -> asyncio.Task | None:
        self.events.append(f"cancel_stream:{reason.strip()}")
        task = self._stream_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
        return task

    async def _await_cancelled_stream_task(
        self,
        task: asyncio.Task | None,
        *,
        session_id: str | None = None,
    ) -> None:
        self.events.append("await_stream")
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=0.05)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass

    async def _cleanup_runtime_locals(
        self, session_id: str, *, finalize_workflows: bool = True
    ) -> None:
        self.events.append(f"cleanup_locals:finalize={finalize_workflows}")

    def clear_active_runtime(self, session_id: str, *, bookmark_paused: bool = False) -> None:
        self.events.append(f"clear_active:bookmark={bookmark_paused}")

    def clear_pending_runtime(self, session_id: str) -> None:
        self.events.append("clear_pending")


@pytest.mark.asyncio
async def test_pause_awaits_runner_pause_then_waits_stream_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _OrderRecorder()

    async def _short_stream() -> None:
        await asyncio.sleep(0)

    recorder._stream_tasks["sess-1"] = asyncio.create_task(_short_stream())

    async def _fake_pause_agent_team(*, team_name: str, session_id: str) -> bool:
        recorder.events.append(f"runner_pause:{team_name}:{session_id}")
        await asyncio.sleep(0)
        # Simulate kernel close_stream: consumer finishes without cancel.
        task = recorder._stream_tasks.get(session_id)
        if task and not task.done():
            await task
        return True

    monkeypatch.setattr(tm_mod.Runner, "pause_agent_team", _fake_pause_agent_team)

    bound = tm_mod.TeamManager.pause_session_runtime.__get__(recorder, tm_mod.TeamManager)
    ok = await bound("sess-1", reason="interrupt(intent=pause): ")
    assert ok is True

    assert recorder.events[0].startswith("runner_pause:")
    assert "wait_stream_exit" in recorder.events
    assert not any(e.startswith("cancel_stream:") for e in recorder.events)
    assert "pause-early" not in " ".join(recorder.events)


@pytest.mark.asyncio
async def test_lifecycle_lock_is_per_session_not_global_bootstrap() -> None:
    mgr = tm_mod.TeamManager()
    lock_a = mgr._get_lifecycle_lock("sess-a")
    lock_b = mgr._get_lifecycle_lock("sess-b")
    assert lock_a is not lock_b
    assert lock_a is not mgr._bootstrap_lock
    assert lock_b is not mgr._bootstrap_lock
