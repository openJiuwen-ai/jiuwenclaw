"""RsiWorker：单协程队列（并发=1）+ 事件链路装配（内部 v3 §4.2）。

- ``enqueue(task_id)``：队列空 → RUNNING 直接启动；有运行任务 → QUEUED（全局并发=1）。
- 启动时：``adapter.build_request(task)`` → 装配事件链路（sink→有界队列→消费协程）→
  ``await adapter.run(request, on_event=sink)``；正常返回 → COMPLETED；异常 → FAILED。
- 终态后 ``await q.join()`` 排空最后事件再关消费协程。
- ``cancel(mode)`` / ``resume()``：中优先级（I7/I8/I9）接口已落位 + 状态机校验，
  运行中的 artifact 任务会转发到 Provider 的 pause/terminate；
  ``resume(fingerprint_check)`` 仍由具体 Provider 负责断点指纹校验。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from jiuwenswarm.agents.harness.common.rsi.errors import (
    RsiDatasetInvalid,
    RsiNotReady,
    RsiScenarioNotSupported,
    RsiTaskStateConflict,
)
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
        self._resume_task_ids: set[str] = set()
        self._control_tasks: dict[str, asyncio.Task[Any]] = {}

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
            self._ensure_runner()
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
        """``pause`` / ``terminate`` and delegate running control to Provider."""
        mode = str(mode or "").lower()
        task = self.store.get(task_id)
        adapter = self._adapter_for(task.scenario, task.artifact_type)
        if mode == "pause":
            if task.scenario == "ARTIFACT" and (
                adapter is None or not bool(getattr(adapter, "supports_pause", False))
            ):
                raise RsiScenarioNotSupported("当前产物场景不支持 pause")
            if task.status not in {TaskStatus.RUNNING.value, TaskStatus.QUEUED.value}:
                self._conflict(task_id, f"状态 {task.status} 不可 pause")
            if task.status == TaskStatus.QUEUED.value:
                self._dequeue_locked(task_id)
                result = self.store.update_status(
                    task_id, [task.status], TaskStatus.PAUSED.value, cause=f"cancel({mode})"
                )
                return result.status
            # A running task remains RUNNING until the Provider confirms the
            # pause.  The control task owns the eventual state transition.
            self._schedule_provider_control(task_id, adapter, "pause")
            return task.status
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
                result = self.store.update_status(
                    task_id, [task.status], TaskStatus.TERMINATED.value, cause=f"cancel({mode})"
                )
                return result.status
            if task.status in {TaskStatus.RUNNING.value, TaskStatus.PAUSED.value} and adapter is not None:
                # Keep the public state unchanged until the Provider confirms
                # termination, including the PAUSED -> TERMINATED path.
                self._schedule_provider_control(task_id, adapter, "terminate")
                return task.status
            result = self.store.update_status(
                task_id, [task.status], TaskStatus.TERMINATED.value, cause=f"cancel({mode})"
            )
            return result.status
        self._conflict(task_id, f"未知 mode: {mode}")

    def resume(self, task_id: str, fingerprint_check: bool = True) -> str:
        """``resume``：校验 + 入队，引擎 fingerprint 校验留给 resume 执行路径（⚠️外部 C2）。"""
        task = self.store.get(task_id)
        if task.status != TaskStatus.PAUSED.value:
            self._conflict(task_id, "仅 PAUSED 可 resume")
        adapter = self._adapter_for(task.scenario, task.artifact_type)
        if task.scenario == "ARTIFACT" and (
            adapter is None or not bool(getattr(adapter, "supports_resume", False))
        ):
            raise RsiScenarioNotSupported("当前产物场景不支持 resume")
        if fingerprint_check:
            # 引擎侧 fingerprint 校验由 HarnessEngineAdapter.resume 落地（C2 ⚠️外部）；
            # 当前无引擎态 → 日志提示，不误报成功（真实校验在 resume 执行路径）
            logger.warning("[RSI] resume fingerprint 校验未装配（C2 ⚠️外部），task=%s", task_id)
        self.store.update_status(task_id, [TaskStatus.PAUSED.value], TaskStatus.QUEUED.value, cause="resume")
        self._resume_task_ids.add(task_id)
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
                resume = task_id in self._resume_task_ids
                self._resume_task_ids.discard(task_id)
                await self._execute_task(task_id, resume=resume)
            except Exception:  # noqa: BLE001 - 单任务状态冲突/异常不拖垮 worker
                logger.exception("[RSI] 任务执行异常 task=%s，跳过继续取下一个", task_id)
            finally:
                self._running_task_id = None
                self._queue.task_done()

    async def _execute_task(self, task_id: str, *, resume: bool = False) -> None:
        task_view = self.store.get_view(task_id)
        adapter = self._adapter_for(task_view.scenario, task_view.artifact_type)
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
            if task_view.scenario == "ARTIFACT" and hasattr(adapter, "validate_input"):
                validation = adapter.validate_input(
                    task_view.artifact_path or task_view.config.get("artifact_path"),
                    scenario=task_view.scenario,
                    artifact_type=task_view.artifact_type,
                )
                if not bool(getattr(validation, "valid", False)):
                    errors = getattr(validation, "errors", None) or []
                    raise RsiDatasetInvalid("Provider 输入校验失败", errors=errors)
            request = adapter.build_request(task_view, resume=resume)
            if resume:
                result = await adapter.resume(request, on_event=_sink(queue))
            else:
                result = await adapter.run(request, on_event=_sink(queue))
            self._apply_result_status(task_id, result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[RSI] 任务执行失败 task=%s: %s", task_id, exc)
            self._mark_failed_if_running(task_id, str(exc)[:200])
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

    def _adapter_for(self, scenario: str | None, artifact_type: str | None) -> Any:
        scenario_key = str(scenario or "").strip().upper()
        artifact_key = str(artifact_type or "").strip().upper()
        candidates: list[str] = []
        if scenario_key == "ARTIFACT":
            if artifact_key:
                candidates.extend(
                    [
                        f"ARTIFACT:{artifact_key}",
                        f"artifact:{artifact_key.lower()}",
                    ]
                )
            candidates.append("ARTIFACT")
        elif scenario_key:
            candidates.extend([scenario_key, scenario_key.lower()])
        for key in candidates:
            adapter = self.adapters.get(key)
            if adapter is not None:
                return adapter
        return None

    def _apply_result_status(self, task_id: str, result: Any) -> None:
        raw_status = getattr(result, "status", None)
        raw_status = getattr(raw_status, "value", raw_status)
        status = str(raw_status or "completed").upper()
        target = {
            "COMPLETED": TaskStatus.COMPLETED.value,
            "FAILED": TaskStatus.FAILED.value,
            "PAUSED": TaskStatus.PAUSED.value,
            "TERMINATED": TaskStatus.TERMINATED.value,
        }.get(status)
        if target is None:
            target = TaskStatus.FAILED.value
        current = self.store.get(task_id).status
        if current != TaskStatus.RUNNING.value:
            return
        self.store.update_status(
            task_id,
            [TaskStatus.RUNNING.value],
            target,
            cause=f"provider.{status.lower()}",
        )

    def _mark_failed_if_running(self, task_id: str, cause: str) -> None:
        try:
            if self.store.get(task_id).status == TaskStatus.RUNNING.value:
                self.store.update_status(
                    task_id,
                    [TaskStatus.RUNNING.value],
                    TaskStatus.FAILED.value,
                    cause=cause,
                )
        except RsiTaskStateConflict:
            # A concurrent pause/terminate owns the final public state.
            logger.debug("[RSI] task state changed while handling failure: %s", task_id)

    def _schedule_provider_control(self, task_id: str, adapter: Any, mode: str) -> None:
        method = getattr(adapter, mode, None)
        if not callable(method):
            raise RsiScenarioNotSupported(f"当前 Provider 不支持 {mode}")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise RsiNotReady(
                f"当前无运行中事件循环，无法调用 Provider.{mode}: task={task_id}"
            )
        previous = self._control_tasks.get(task_id)
        if previous is not None and not previous.done():
            logger.warning(
                "[RSI] Provider.%s task=%s 将在前一个控制完成后执行",
                mode,
                task_id,
            )

        async def _invoke() -> Any:
            if previous is not None:
                try:
                    await previous
                except asyncio.CancelledError:
                    if not previous.cancelled():
                        raise
                except Exception as exc:  # noqa: BLE001 - continue queued control
                    logger.warning(
                        "[RSI] 前一个 Provider 控制失败，继续执行 Provider.%s task=%s: %s",
                        mode,
                        task_id,
                        exc,
                    )
            result = await method(task_id)
            self._apply_control_result(task_id, mode, result)
            return result

        control_task = loop.create_task(_invoke())
        self._control_tasks[task_id] = control_task

        def _control_done(done: asyncio.Task[Any]) -> None:
            if self._control_tasks.get(task_id) is done:
                self._control_tasks.pop(task_id, None)
            if done.cancelled():
                logger.warning("[RSI] Provider.%s control cancelled task=%s", mode, task_id)
                return
            try:
                done.result()
            except Exception as exc:  # noqa: BLE001 - keep worker alive
                logger.warning("[RSI] Provider.%s failed task=%s: %s", mode, task_id, exc)

        control_task.add_done_callback(_control_done)

    def _apply_control_result(self, task_id: str, mode: str, result: Any) -> None:
        """Commit the public state only after a Provider control returns."""
        raw_status = getattr(result, "status", result)
        raw_status = getattr(raw_status, "value", raw_status)
        status = str(raw_status or "").upper()
        target = {
            "COMPLETED": TaskStatus.COMPLETED.value,
            "FAILED": TaskStatus.FAILED.value,
            "PAUSED": TaskStatus.PAUSED.value,
            "TERMINATED": TaskStatus.TERMINATED.value,
        }.get(status)
        if target is None:
            logger.warning(
                "[RSI] Provider.%s returned an unusable status task=%s status=%r",
                mode,
                task_id,
                raw_status,
            )
            return
        try:
            current = self.store.get(task_id).status
            if current == target:
                return
            allowed_targets = {
                TaskStatus.RUNNING.value: {
                    TaskStatus.COMPLETED.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.PAUSED.value,
                    TaskStatus.TERMINATED.value,
                },
                TaskStatus.PAUSED.value: {TaskStatus.TERMINATED.value},
            }
            if target not in allowed_targets.get(current, set()):
                logger.debug(
                    "[RSI] Provider.%s result ignored for task=%s current=%s target=%s",
                    mode,
                    task_id,
                    current,
                    target,
                )
                return
            self.store.update_status(
                task_id,
                [current],
                target,
                cause=f"provider.{status.lower()}",
            )
        except RsiTaskStateConflict:
            # The run path or another control already owns the final state.
            logger.debug("[RSI] task state changed while applying Provider.%s: %s", mode, task_id)

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
                "best_artifact_path",
                "published_artifact_path",
                "final_node_id",
                "error_code",
                "error_message",
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
