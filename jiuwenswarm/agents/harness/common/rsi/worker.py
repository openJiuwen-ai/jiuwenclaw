"""RsiWorker：单协程队列（并发=1）+ 事件链路装配（内部 v3 §4.2）。

- ``enqueue(task_id)``：队列空 → RUNNING 直接启动；有运行任务 → QUEUED（全局并发=1）。
- 启动时：``adapter.build_request(task)`` → 装配事件链路（sink→有界队列→消费协程）→
  ``await adapter.run(request, on_event=sink)``；正常返回 → COMPLETED；异常 → FAILED。
- 终态后 ``await q.join()`` 排空最后事件再关消费协程。
- ``cancel(mode)`` / ``resume()``：中优先级（I7/I8/I9）接口已落位 + 状态机校验，
  **协程取消/引擎衔接为 ⚠️外部/架构 TODO**（内部 v3 §4.2 注：pause↔引擎衔接为架构 TODO，
  接口已为 ``resume(fingerprint_check)`` 预留校验参数）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from jiuwenswarm.agents.harness.common.rsi.errors import RsiTaskStateConflict
from jiuwenswarm.agents.harness.common.rsi.event_consumer import RsiEventConsumer, consume_queue
from jiuwenswarm.agents.harness.common.rsi.events import EngineEvent
from jiuwenswarm.agents.harness.common.rsi.models import TaskStatus

logger = logging.getLogger(__name__)

_QUEUE_MAXSIZE = 128


class RsiWorker:
    """全局单队列 worker（并发=1），跨场景共享。

    Args:
        store: RsiTaskStore
        adapters: scenario → RsiEngineAdapter（HARNESS/ARTIFACT）
        usage_recorder / projector / artifact_service: 事件链路消费方
        push_callbacks: ``{event_type: async (event_type, task_id, payload)->None}``
            （AgentServer 注入 send_push 包装）
    """

    def __init__(
        self,
        store: Any,
        adapters: dict[str, Any],
        usage_recorder: Any,
        projector: Any,
        artifact_service: Any,
        push_callbacks: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.adapters = adapters
        self.usage_recorder = usage_recorder
        self.projector = projector
        self.artifact_service = artifact_service
        self._push_callbacks = push_callbacks or {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._running_task_id: str | None = None
        self._run_task: asyncio.Task[Any] | None = None
        self._last_enqueued: str | None = None

    # -- 队列 --

    def enqueue(self, task_id: str) -> str:
        """入队（内部 v3 §4.2 语义）。返回 QUEUED。

        - 单一路径：任何非 PAUSED / 非 QUEUED 任务统一 CREATED→QUEUED 并入队；
          ``_ensure_runner`` 在运行中时自动跳过（并发=1 语义）。
        - 已排队任务幂等短路，避免状态机 QUEUED→QUEUED 自冲突。
        """
        task = self.store.get(task_id)
        if task.status == TaskStatus.PAUSED.value:
            self._conflict(task_id, "PAUSED 任务请走 resume")
        if task.status == TaskStatus.QUEUED.value:
            return TaskStatus.QUEUED.value
        self.store.update_status(
            task_id,
            [TaskStatus.CREATED.value],
            TaskStatus.QUEUED.value,
            cause="enqueue",
        )
        self._last_enqueued = task_id
        self._queue.put_nowait(task_id)
        self._ensure_runner()
        return TaskStatus.QUEUED.value

    # -- 取消 / 恢复（中优先级，接口落位） --

    def cancel(self, task_id: str, mode: str) -> str:
        """``pause`` / ``terminate``。状态机部分完整实现（含排队中出队）；协程取消为 TODO。"""
        mode = str(mode or "").lower()
        task = self.store.get(task_id)
        if mode == "pause":
            if task.status not in {TaskStatus.RUNNING.value, TaskStatus.QUEUED.value}:
                self._conflict(task_id, f"状态 {task.status} 不可 pause")
            if task.status == TaskStatus.QUEUED.value:
                self._dequeue_locked(task_id)
            else:
                # RUNNING：引擎衔接（asyncio cancel + state 保留）为架构 TODO（内部 v3 §4.2）
                logger.warning("[RSI] pause 引擎衔接 TODO: task=%s（协程取消未装配）", task_id)
            return self.store.update_status(
                task_id, [task.status], TaskStatus.PAUSED.value, cause=f"cancel({mode})"
            ).status
        if mode == "terminate":
            if task.status not in {
                TaskStatus.CREATED.value,
                TaskStatus.RUNNING.value,
                TaskStatus.QUEUED.value,
                TaskStatus.PAUSED.value,
            }:
                self._conflict(task_id, f"状态 {task.status} 不可 terminate")
            if task.status == TaskStatus.QUEUED.value:
                self._dequeue_locked(task_id)
            return self.store.update_status(
                task_id, [task.status], TaskStatus.TERMINATED.value, cause=f"cancel({mode})"
            ).status
        self._conflict(task_id, f"未知 mode: {mode}")

    def resume(self, task_id: str, fingerprint_check: bool = True) -> str:
        """``resume``：校验 + 入队，引擎 fingerprint 校验留给 resume 执行路径（⚠️外部 C2）。"""
        task = self.store.get(task_id)
        if task.status != TaskStatus.PAUSED.value:
            self._conflict(task_id, "仅 PAUSED 可 resume")
        if fingerprint_check:
            # 引擎侧 fingerprint 校验由 HarnessEngineAdapter.resume 落地（C2 ⚠️外部）；
            # 当前无引擎态 → 日志提示，不误报成功（真实校验在 resume 执行路径）
            logger.warning("[RSI] resume fingerprint 校验未装配（C2 ⚠️外部），task=%s", task_id)
        self.store.update_status(task_id, [TaskStatus.PAUSED.value], TaskStatus.QUEUED.value, cause="resume")
        self._last_enqueued = task_id
        self._queue.put_nowait(task_id)
        self._ensure_runner()
        return TaskStatus.QUEUED.value

    # -- 内部 --

    def _ensure_runner(self) -> None:
        if self._run_task is not None and not self._run_task.done():
            return
        try:
            self._run_task = asyncio.create_task(self._run_loop())
        except RuntimeError:
            # 无运行中事件循环（同步上下文/单测/冷启动脚本）：入队成功但执行延后，
            # 由事件循环就绪后的下一次 enqueue 或显式 _ensure_runner 接管（内部 v3 §4.2）。
            logger.warning("[RSI] 无运行中事件循环，任务已入队，等待事件循环接管: task=%s", getattr(self, "_last_enqueued", ""))
            self._run_task = None

    async def _run_loop(self) -> None:
        while True:
            task_id = await self._queue.get()
            self._running_task_id = task_id
            try:
                self.store.update_status(
                    task_id,
                    [TaskStatus.QUEUED.value, TaskStatus.PAUSED.value],
                    TaskStatus.RUNNING.value,
                    cause="worker.start",
                )
                await self._execute_task(task_id)
            except Exception:  # noqa: BLE001 - 单任务状态冲突/异常不拖垮 worker
                logger.exception("[RSI] 任务执行异常 task=%s，跳过继续取下一个", task_id)
            finally:
                self._running_task_id = None
                self._queue.task_done()

    async def _execute_task(self, task_id: str) -> None:
        task_view = self.store.get_view(task_id)
        adapter = self.adapters.get(task_view.scenario)
        if adapter is None:
            self.store.update_status(
                task_id, [TaskStatus.RUNNING.value], TaskStatus.FAILED.value,
                cause=f"scenario adapter not registered: {task_view.scenario}",
            )
            return
        queue: asyncio.Queue[EngineEvent | None] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        consumer = RsiEventConsumer(
            task_id=task_id,
            usage_recorder=self.usage_recorder,
            projector=self.projector,
            artifact_service=self.artifact_service,
        )
        consumer.bind_push(
            on_progress=self._push("rsi.training.progress"),
            on_tree_delta=self._push("rsi.training.tree.delta"),
        )
        consume_task = asyncio.create_task(consume_queue(queue, consumer))
        result: Any = None
        try:
            request = adapter.build_request(task_view)
            result = await adapter.run(request, on_event=_sink(queue))
            self.store.update_status(
                task_id, [TaskStatus.RUNNING.value], TaskStatus.COMPLETED.value, cause="worker.complete"
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[RSI] 任务执行失败 task=%s: %s", task_id, exc)
            self.store.update_status(
                task_id, [TaskStatus.RUNNING.value], TaskStatus.FAILED.value, cause=str(exc)[:200]
            )
        finally:
            try:
                await queue.join()
            except Exception:  # noqa: BLE001
                pass
            queue.put_nowait(None)
            try:
                await consume_task
            except Exception:  # noqa: BLE001
                logger.exception("[RSI] 事件消费协程退出异常 task=%s", task_id)
            self._persist_results(task_id, result)

    def _persist_results(self, task_id: str, result: Any) -> None:
        """引擎结果落盘（IterativeSingleHarnessResult 形状）→ task.json.config.results。

        ``merge_results`` 由 store 在锁内完成读-改-写，避免与 update_status /
        mark_active_ref_released 并发写同一 task.json（PR !5798 #3）。
        """
        if result is None:
            return
        try:
            results: dict[str, Any] = {}
            for key in (
                "state_path",
                "report_path",
                "current_harness_refs_path",
                "best_harness_refs_path",
                "published_harness_refs_path",
                "best_score",
            ):
                value = getattr(result, key, None)
                if value is not None:
                    results[key] = str(value) if not isinstance(value, float) else value
            self.store.merge_results(task_id, results)
        except Exception:  # noqa: BLE001
            logger.exception("[RSI] 持久化引擎结果失败 task=%s", task_id)

    def _push(self, event_type: str):
        callback = self._push_callbacks.get(event_type)

        async def _send(task_id: str, payload: dict[str, Any]) -> None:
            if callback is None:
                return
            await callback(event_type, task_id, payload)

        return _send

    def _dequeue_locked(self, task_id: str) -> None:
        """排队中出队：从队列移除该 task（保持其余顺序）。"""
        remaining: list[str] = []
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item != task_id:
                remaining.append(item)
            else:
                self._queue.task_done()
        for item in remaining:
            self._queue.put_nowait(item)

    @staticmethod
    def _conflict(task_id: str, message: str) -> None:
        raise RsiTaskStateConflict(message)


def _sink(queue: asyncio.Queue[EngineEvent | None]):
    """事件入队闭包（引擎 on_event → 有界队列）。"""

    async def _put(event: EngineEvent) -> None:
        queue.put_nowait(event)

    return _put