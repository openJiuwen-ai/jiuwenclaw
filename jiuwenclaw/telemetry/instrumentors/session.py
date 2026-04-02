# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Instrumentor for JiuWenClaw session lifecycle — SESSION metrics.

Tracks session state transitions (created/active/idle/cancelled/destroyed)
and detects stuck sessions via periodic checking.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from typing import Any, Dict

from jiuwenclaw.utils import logger
from jiuwenclaw.telemetry.attributes import (
    JIUWENCLAW_SESSION_ID,
    JIUWENCLAW_SESSION_STATE,
    JIUWENCLAW_SESSION_STATE_REASON,
)
from jiuwenclaw.telemetry.metrics import (
    session_created_count,
    session_state_count,
    session_stuck_count,
    session_stuck_age,
    set_session_active_observer,
)

# Module-level config — set by instrument_session()
_stuck_threshold_ms: float = 300000.0
_stuck_check_interval_s: float = 30.0
_tracked_agent_servers: weakref.WeakSet[Any] = weakref.WeakSet()


def _emit_state(session_id: str, state: str, reason: str) -> None:
    """Record a session state transition."""
    session_state_count.add(1, {
        JIUWENCLAW_SESSION_ID: session_id,
        JIUWENCLAW_SESSION_STATE: state,
        JIUWENCLAW_SESSION_STATE_REASON: reason,
    })


def _count_active_sessions() -> int:
    """Count active session processors across tracked agent instances."""
    active_sessions = 0
    for agent_server in list(_tracked_agent_servers):
        processors = getattr(agent_server, "_session_processors", {}) or {}
        active_sessions += sum(
            1
            for task in processors.values()
            if task is not None and not task.done()
        )
    return active_sessions


def instrument_session(
    stuck_threshold_ms: float = 300000.0,
    stuck_check_interval_s: float = 30.0,
) -> None:
    """Monkey-patch JiuWenClaw to collect session state/stuck metrics."""
    global _stuck_threshold_ms, _stuck_check_interval_s
    _stuck_threshold_ms = float(stuck_threshold_ms)
    _stuck_check_interval_s = float(stuck_check_interval_s)
    set_session_active_observer(_count_active_sessions)

    try:
        from jiuwenclaw.agentserver.interface import JiuWenClaw
    except ImportError:
        logger.debug("[Telemetry] JiuWenClaw not available, skipping session instrumentor")
        return

    _original_init = JiuWenClaw.__init__
    _original_ensure_processor = JiuWenClaw._ensure_session_processor
    _original_cancel_task = JiuWenClaw._cancel_session_task

    # --- Patch __init__: add tracking dicts ---
    def _patched_init(self, *args, **kwargs):
        _original_init(self, *args, **kwargs)
        self._session_task_start_times: Dict[str, float] = {}
        self._stuck_reported: Dict[str, bool] = {}
        self._stuck_checker_task: asyncio.Task | None = None
        _tracked_agent_servers.add(self)

    # --- Patch _ensure_session_processor: replace with instrumented version ---
    async def _patched_ensure_session_processor(self, session_id: str) -> None:
        if session_id not in self._session_processors or self._session_processors[session_id].done():
            # 创建新的优先级队列和计数器
            self._session_queues[session_id] = asyncio.PriorityQueue()
            self._session_priorities[session_id] = 0

            # >>> 埋点: state=created
            session_created_count.add(1)
            _emit_state(session_id, "created", "new_request")

            # 创建任务处理器
            async def process_session_queue():
                """处理 session 任务队列（先进后出执行，新任务优先）."""
                queue = self._session_queues[session_id]
                while True:
                    try:
                        # 从队列获取任务（优先级高的先执行）
                        priority, task_func = await queue.get()
                        if task_func is None:  # 信号：关闭队列
                            break

                        # >>> 埋点: state=active
                        self._session_task_start_times[session_id] = time.monotonic()
                        self._stuck_reported.pop(session_id, None)
                        _emit_state(session_id, "active", "task_started")

                        # 执行任务
                        self._session_tasks[session_id] = asyncio.create_task(task_func())
                        try:
                            await self._session_tasks[session_id]
                            # >>> 埋点: state=idle, reason=task_completed
                            _emit_state(session_id, "idle", "task_completed")
                        except asyncio.CancelledError:
                            # cancelled 状态在 _cancel_session_task patch 中记录
                            pass
                        except Exception:
                            # >>> 埋点: state=idle, reason=task_error
                            _emit_state(session_id, "idle", "task_error")
                        finally:
                            self._session_tasks[session_id] = None
                            self._session_task_start_times.pop(session_id, None)
                            queue.task_done()

                    except asyncio.CancelledError:
                        logger.info("[JiuWenClaw] Session 任务处理器被取消: session_id=%s", session_id)
                        break
                    except Exception as e:
                        logger.error("[JiuWenClaw] Session 任务处理器异常: %s", e)

                # 清理
                self._session_queues.pop(session_id, None)
                self._session_priorities.pop(session_id, None)
                self._session_tasks.pop(session_id, None)
                self._session_processors.pop(session_id, None)
                self._session_task_start_times.pop(session_id, None)
                self._stuck_reported.pop(session_id, None)

                # >>> 埋点: state=destroyed
                _emit_state(session_id, "destroyed", "queue_closed")
                logger.info("[JiuWenClaw] Session 任务处理器已关闭: session_id=%s", session_id)

            self._session_processors[session_id] = asyncio.create_task(process_session_queue())

            # 确保 stuck checker 在运行
            _ensure_stuck_checker(self)

    # --- Patch _cancel_session_task: add state=cancelled metric ---
    async def _patched_cancel_session_task(self, session_id: str, log_msg_prefix: str = "") -> None:
        task = self._session_tasks.get(session_id)
        if task is not None and not task.done():
            # >>> 埋点: state=cancelled
            _emit_state(session_id, "cancelled", "user_cancel")
            self._session_task_start_times.pop(session_id, None)
            self._stuck_reported.pop(session_id, None)

        await _original_cancel_task(self, session_id, log_msg_prefix)

    # Apply patches
    JiuWenClaw.__init__ = _patched_init
    JiuWenClaw._ensure_session_processor = _patched_ensure_session_processor
    JiuWenClaw._cancel_session_task = _patched_cancel_session_task


def _ensure_stuck_checker(agent_server) -> None:
    """Start the periodic stuck session checker if not already running."""
    checker = getattr(agent_server, "_stuck_checker_task", None)
    if checker is not None and not checker.done():
        return

    async def _check_stuck_sessions():
        while True:
            try:
                await asyncio.sleep(_stuck_check_interval_s)
                now = time.monotonic()
                start_times = getattr(agent_server, "_session_task_start_times", {})
                stuck_reported = getattr(agent_server, "_stuck_reported", {})

                for sid, start in list(start_times.items()):
                    age_ms = (now - start) * 1000
                    if age_ms > _stuck_threshold_ms:
                        # Always record age histogram
                        session_stuck_age.record(age_ms, {JIUWENCLAW_SESSION_ID: sid})
                        # Only count first detection
                        if not stuck_reported.get(sid):
                            session_stuck_count.add(1, {JIUWENCLAW_SESSION_ID: sid})
                            stuck_reported[sid] = True
                            logger.warning(
                                "[Telemetry] Session stuck detected: session_id=%s, age_ms=%.0f",
                                sid, age_ms,
                            )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("[Telemetry] Stuck checker error: %s", e)

    agent_server._stuck_checker_task = asyncio.create_task(_check_stuck_sessions())
