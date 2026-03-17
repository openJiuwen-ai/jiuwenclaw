# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, Optional

import anyio

from openjiuwen.core.common.logging import LogEventType, runner_logger as logger
from openjiuwen.core.common.task_manager.context import _current_task_id
from openjiuwen.core.common.task_manager.types import TaskStatus, TERMINAL_STATES


@dataclass
class Task:
    """Coroutine task data model"""
    task_id: str
    name: Optional[str] = None
    group: Optional[str] = None
    parent_task_id: Optional[str] = None

    status: TaskStatus = TaskStatus.PENDING
    timeout: Optional[float] = None

    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    result: Optional[Any] = None
    exception: Optional[BaseException] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    _done_event: anyio.Event = field(default_factory=anyio.Event, init=False, repr=False)
    _cancel_scope: Optional[anyio.CancelScope] = field(default=None, init=False, repr=False)

    cancelled_by: Optional[str] = None
    cancel_reason: Optional[str] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    def __hash__(self) -> int:
        return hash(self.task_id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Task):
            return NotImplemented
        return self.task_id == other.task_id

    @property
    def display_name(self) -> str:
        return self.name or self.task_id[:8]

    @property
    def error(self) -> Optional[str]:
        return str(self.exception) if self.exception else None

    async def wait(self) -> Any:
        await self._done_event.wait()
        if self.exception:
            raise self.exception
        return self.result

    def set_cancel_scope(self, cancel_scope: anyio.CancelScope) -> None:
        self._cancel_scope = cancel_scope

    def clear_cancel_scope(self) -> None:
        self._cancel_scope = None

    def get_cancel_scope(self) -> Optional[anyio.CancelScope]:
        return self._cancel_scope

    async def cancel(self, cascade: bool = False, reason: Optional[str] = None) -> bool:
        """Cancel this task

        Args:
            cascade: Whether to also cancel child tasks
            reason: Optional reason for cancellation

        Returns:
            True if cancellation was triggered, False otherwise
        """
        if not self._cancel_scope:
            return False

        self._cancel_scope.cancel()
        self.cancel_reason = reason or "manual_cancel"

        if cascade:
            # Import here to avoid circular imports
            from openjiuwen.core.common.task_manager.manager import get_task_manager
            manager = get_task_manager()
            # Use "parent_cancelled" for cascade, consistent with previous behavior
            await manager.cascade_cancel(self.task_id, reason="parent_cancelled")

        return True

    def abort(self, reason: Optional[str] = None) -> bool:
        """Abort this task synchronously (local cancellation only, no cascade)

        This is a synchronous version of cancel() that doesn't support cascade.
        Use this when you need to cancel without awaiting.

        Args:
            reason: Optional reason for cancellation

        Returns:
            True if cancellation was triggered, False otherwise
        """
        if not self._cancel_scope:
            return False

        self._cancel_scope.cancel()
        self.cancel_reason = reason or "manual_cancel"
        return True

    def set_done(self) -> None:
        self._done_event.set()

    async def execute(
        self,
        coro: Coroutine,
        callback_trigger: Optional[Callable] = None,
        catch_exceptions: bool = False,
    ) -> Any:
        """Execute the coroutine with task lifecycle management.

        Args:
            coro: The coroutine to execute
            callback_trigger: Optional callback for triggering events
            catch_exceptions: If True, catch and log exceptions instead of raising
        """
        async def _execute_core():
            token = _current_task_id.set(self.task_id)
            self.status = TaskStatus.RUNNING
            self.started_at = datetime.now(timezone.utc)

            # Trigger event
            if callback_trigger:
                await callback_trigger(self, "running")

            try:
                result = None
                with anyio.CancelScope() as cancel_scope:
                    self.set_cancel_scope(cancel_scope)

                    if self.timeout:
                        with anyio.fail_after(self.timeout):
                            result = await coro
                    else:
                        result = await coro

                self.result = result
                self.status = TaskStatus.COMPLETED
                self.finished_at = datetime.now(timezone.utc)

                if callback_trigger:
                    await callback_trigger(self, "completed")

                return result

            except asyncio.CancelledError:
                self.status = TaskStatus.CANCELLED
                self.finished_at = datetime.now(timezone.utc)
                if callback_trigger:
                    await callback_trigger(self, "cancelled")
                raise

            except TimeoutError:
                self.status = TaskStatus.TIMEOUT
                self.exception = TimeoutError("Task timeout")
                self.finished_at = datetime.now(timezone.utc)
                if callback_trigger:
                    await callback_trigger(self, "timeout")
                raise

            except Exception as e:
                self.status = TaskStatus.FAILED
                self.exception = e
                self.finished_at = datetime.now(timezone.utc)
                if callback_trigger:
                    await callback_trigger(self, "failed")
                raise

            finally:
                self.set_done()
                self.clear_cancel_scope()
                _current_task_id.reset(token)

        if catch_exceptions:
            try:
                return await _execute_core()
            except Exception as e:
                logger.error(
                    "Task failed",
                    event_type=LogEventType.CORO_MANAGER_TASK_STATUS_CHANGED,
                    exception=e,
                    metadata={"task_id": self.task_id, "name": self.name,
                              "previous_status": "running", "current_status": "failed"},
                )
                return None
        else:
            return await _execute_core()
