# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Session Manager - 管理 session 任务队列和并发控制.

提供：
- Session 任务队列管理（先进后出，新任务优先）
- Session 任务执行器
- Session 任务取消
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class SessionManager:
    """Session 任务管理器.

    管理多 session 并发执行，同 session 内任务按先进后出顺序执行.
    """

    def __init__(self) -> None:
        self._session_tasks: dict[str, asyncio.Task] = {}
        self._session_priorities: dict[str, int] = {}
        self._session_queues: dict[str, asyncio.PriorityQueue] = {}
        self._session_processors: dict[str, asyncio.Task] = {}

    @staticmethod
    def get_session_id(session_id: str | None) -> str:
        """获取 session_id，默认为 'default'."""
        return session_id or "default"

    @staticmethod
    def _is_oneshot_session(session_id: str) -> bool:
        """判断是否为一次性 session（心跳/定时任务），其 session_id 永不复用.

        这类 session 每次都用全新 session_id，任务执行完后 processor 不会再有
        新任务进来，必须主动回收，否则 processor 协程永久阻塞在 queue.get()，
        连同队列/字典条目泄漏。判定口径与 interface_deep 中一致.
        """
        return session_id.startswith("heartbeat") or session_id.startswith("cron")

    async def cancel_session_task(
        self,
        session_id: str,
        log_msg_prefix: str = "",
        wait_timeout: float | None = None,
    ) -> None:
        """取消指定 session 的非流式任务."""
        task = self._session_tasks.get(session_id)
        if task is not None and not task.done():
            logger.info(
                "[SessionManager] %s取消 session 非流式任务: session_id=%s",
                log_msg_prefix,
                session_id,
            )
            task.cancel()
            try:
                if wait_timeout is None:
                    await task
                else:
                    await asyncio.wait_for(task, timeout=wait_timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "[SessionManager] %scancel_session_task wait timeout: session_id=%s wait_timeout=%s",
                    log_msg_prefix,
                    session_id,
                    wait_timeout,
                )
            except (asyncio.CancelledError, Exception):
                pass
            self._session_tasks[session_id] = None
            logger.info(
                "[SessionManager] %ssession task terminated: session_id=%s",
                log_msg_prefix,
                session_id,
            )

    async def cancel_all_session_tasks(self, log_msg_prefix: str = "") -> None:
        """取消所有 session 的非流式任务."""
        for session_id in list(self._session_tasks.keys()):
            await self.cancel_session_task(session_id, log_msg_prefix)

    async def ensure_session_processor(self, session_id: str) -> None:
        """确保 session 的任务处理器在运行."""
        if (
            session_id not in self._session_processors
            or self._session_processors[session_id].done()
        ):
            self._session_queues[session_id] = asyncio.PriorityQueue()
            self._session_priorities[session_id] = 0

            async def process_session_queue():
                """处理 session 任务队列（先进后出执行，新任务优先）."""
                queue = self._session_queues[session_id]
                while True:
                    try:
                        item = await queue.get()
                        # Queue items are (priority, task_func, ctx) tuples.
                        # The sentinel to stop the processor is (priority, None, None).
                        priority, task_func, task_ctx = item
                        if task_func is None:
                            break

                        # Pass the captured ContextVar context to create_task
                        # so the new Task inherits the caller's ContextVars
                        # (workspace, cwd, project_root, etc.) rather than
                        # the processor Task's (possibly stale) context.
                        self._session_tasks[session_id] = asyncio.create_task(
                            task_func(), context=task_ctx
                        )
                        try:
                            await self._session_tasks[session_id]
                        finally:
                            self._session_tasks[session_id] = None
                            queue.task_done()

                    except asyncio.CancelledError:
                        logger.info(
                            "[SessionManager] Session 任务处理器被取消: session_id=%s",
                            session_id,
                        )
                        break
                    except Exception as e:
                        logger.error("[SessionManager] Session 任务处理器异常: %s", e)

                self._session_queues.pop(session_id, None)
                self._session_priorities.pop(session_id, None)
                self._session_tasks.pop(session_id, None)
                self._session_processors.pop(session_id, None)
                logger.info(
                    "[SessionManager] Session 任务处理器已关闭: session_id=%s",
                    session_id,
                )

            self._session_processors[session_id] = asyncio.create_task(
                process_session_queue()
            )

    async def submit_task(
        self,
        session_id: str,
        task_func: Callable[[], Awaitable[Any]],
    ) -> None:
        """提交任务到 session 队列.

        Args:
            session_id: Session ID.
            task_func: 异步任务函数.
        """
        await self.ensure_session_processor(session_id)
        self._session_priorities[session_id] -= 1
        priority = self._session_priorities[session_id]
        # Snapshot ContextVars so the agent task inherits the caller's
        # context (workspace, cwd, project_root set by init_cwd, etc.)
        ctx = contextvars.copy_context()
        await self._session_queues[session_id].put((priority, task_func, ctx))

    async def submit_and_wait(
        self,
        session_id: str,
        task_func: Callable[[], Awaitable[Any]],
    ) -> Any:
        """提交任务到 session 队列并等待结果.

        Args:
            session_id: Session ID.
            task_func: 异步任务函数.

        Returns:
            任务执行结果.
        """
        await self.ensure_session_processor(session_id)
        result_future = asyncio.get_event_loop().create_future()

        async def wrapped_task():
            try:
                result = await task_func()
                result_future.set_result(result)
            except Exception as e:
                result_future.set_exception(e)

        self._session_priorities[session_id] -= 1
        priority = self._session_priorities[session_id]
        # Snapshot ContextVars so the agent task inherits the caller's
        # context (workspace, cwd, project_root set by init_cwd, etc.)
        ctx = contextvars.copy_context()
        await self._session_queues[session_id].put((priority, wrapped_task, ctx))

        try:
            return await result_future
        finally:
            # 一次性 session（heartbeat/cron）session_id 永不复用，任务结束后
            # 不会再有新任务进来。这里发一个 None 哨兵让 processor 退出 while 循环，
            # 走既有清理逻辑回收队列/字典条目，避免 processor 协程永久泄漏。
            # 哨兵用较大正数优先级，确保排在所有已入队任务之后执行（不抢占未跑的任务）。
            if self._is_oneshot_session(session_id):
                queue = self._session_queues.get(session_id)
                if queue is not None:
                    await queue.put((1_000_000_000, None, None))

    def get_current_task(self, session_id: str) -> asyncio.Task | None:
        """获取当前 session 正在执行的任务."""
        return self._session_tasks.get(session_id)

    def has_active_processor(self, session_id: str) -> bool:
        """检查 session 是否有活跃的处理器."""
        return (
            session_id in self._session_processors
            and not self._session_processors[session_id].done()
        )

    def has_active_tasks(self) -> bool:
        """是否有活跃的 session 任务（供 dreaming busy_checker 使用）。"""
        return any(t is not None and not t.done() for t in self._session_tasks.values())
