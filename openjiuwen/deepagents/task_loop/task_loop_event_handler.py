# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgent event-handler implementation.

Routes core EventQueue events through the TaskScheduler
pipeline and updates TaskPlan state accordingly.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, Optional, TYPE_CHECKING

from openjiuwen.core.common.logging import logger
from openjiuwen.core.controller.modules.event_handler import (
    EventHandler,
    EventHandlerInput,
)
from openjiuwen.core.controller.schema.event import (
    InputEvent,
    TaskInteractionEvent,
)
from openjiuwen.core.controller.schema.task import (
    Task as CoreTask,
    TaskStatus as CoreTaskStatus,
)
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
)
from openjiuwen.deepagents.task_loop.task_loop_event_executor import (
    DEEP_TASK_TYPE,
)
from openjiuwen.deepagents.task_loop.loop_queues import (
    LoopQueues,
)
from openjiuwen.deepagents.schema.state import (
    load_state,
)

if TYPE_CHECKING:
    from openjiuwen.deepagents.deep_agent import (
        DeepAgent,
    )


class TaskLoopEventHandler(EventHandler):
    """EventHandler that drives the outer task loop.

    Creates core Tasks via TaskManager so that the
    TaskScheduler can pick them up and dispatch to
    TaskLoopEventExecutor.

    Uses a per-round Future pattern: each iteration of
    the outer loop creates a new asyncio.Future via
    prepare_round(), and completion/failed/abort events
    resolve that Future. A monotonic round_id prevents
    stale completions from resolving the wrong Future.

    Attributes:
        _deep_agent: Back-reference to the owning
            DeepAgent instance.
        _last_result: Result of the most recent
            iteration (used by the outer loop).
        _current_future: The Future for the current
            round, created by prepare_round().
        _round_id: Monotonic counter for correlation.
    """

    def __init__(
        self, deep_agent: "DeepAgent"
    ) -> None:
        super().__init__()
        self._deep_agent = deep_agent
        self._last_result: Optional[
            Dict[str, Any]
        ] = None
        # Per-round Future + monotonic round_id
        self._current_future: Optional[
            asyncio.Future
        ] = None
        self._round_id: int = 0
        self._interaction_queues: Optional[
            LoopQueues
        ] = None

    @property
    def last_result(
        self,
    ) -> Optional[Dict[str, Any]]:
        """Result from the last handle_input call."""
        return self._last_result

    @property
    def interaction_queues(
        self,
    ) -> Optional[LoopQueues]:
        """Interaction queues for steer/follow_up."""
        return self._interaction_queues

    @interaction_queues.setter
    def interaction_queues(
        self,
        queues: Optional[LoopQueues],
    ) -> None:
        """Set interaction queues used by task-loop."""
        self._interaction_queues = queues

    def prepare_round(self) -> int:
        """Create a new Future for this round.

        Must be called BEFORE publish_event_async.
        Any previous unresolved Future is cancelled.

        Returns:
            The round_id for correlation.
        """
        if (
            self._current_future is not None
            and not self._current_future.done()
        ):
            self._current_future.cancel()
        self._round_id += 1
        self._current_future = asyncio.Future()
        return self._round_id

    async def wait_completion(
        self,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Await the current round's Future.

        Args:
            timeout: Max seconds to wait.
                None means no limit.

        Returns:
            Result dict. On timeout returns error dict.
        """
        if self._current_future is None:
            return {"error": "no active round"}
        try:
            if timeout is not None:
                result = await asyncio.wait_for(
                    self._current_future,
                    timeout=timeout,
                )
            else:
                result = await self._current_future
        except asyncio.TimeoutError:
            result = {"error": "completion_timeout"}
        except asyncio.CancelledError:
            result = {"error": "cancelled"}
        if not result:
            result = {"status": "completed"}
        self._last_result = result
        return result

    def _resolve_future(
        self,
        result: Dict[str, Any],
        round_id: int,
    ) -> None:
        """Resolve the current round's Future.

        Only resolves if round_id matches the current
        _round_id. This prevents a stale completion
        from accidentally resolving the next round's
        Future.

        Args:
            result: Result dict to set.
            round_id: The round this result belongs to.
        """
        if round_id != self._round_id:
            logger.warning(
                f"Stale resolve: round_id={round_id} "
                f"!= current={self._round_id}, "
                f"discarding result"
            )
            return
        if (
            self._current_future is not None
            and not self._current_future.done()
        ):
            self._current_future.set_result(result)

    async def handle_input(
        self,
        inputs: EventHandlerInput,
    ) -> Optional[Dict]:
        """Create a core Task for scheduling.

        Does NOT await completion — the outer loop uses
        prepare_round() + wait_completion() instead.
        All error paths resolve the current Future so
        the caller never hangs.

        Args:
            inputs: Contains the InputEvent and Session.

        Returns:
            Ack dict with status and task_id.
        """
        agent = self._deep_agent
        event = inputs.event
        session = inputs.session

        # Read round_id from event metadata (set by
        # the publisher in _run_task_loop_core BEFORE
        # publish_event_async). Falls back to current
        # _round_id when key is absent.
        current_round = (
            event.metadata.get(
                "_handler_round_id", self._round_id
            )
            if event.metadata
            else self._round_id
        )

        # Extract query from InputEvent
        query = self._extract_query(event)
        task_id = (
            event.metadata.get("task_id")
            if event.metadata
            else None
        )

        coordinator = agent.loop_coordinator
        if coordinator is None:
            logger.warning(
                "handle_input called without "
                "LoopCoordinator"
            )
            self._resolve_future(
                {"error": "no LoopCoordinator"},
                current_round,
            )
            return {"status": "failed"}

        # Resolve task_id from TaskPlan if available
        # (skip for follow_up — use random UUID)
        is_follow_up = (
            event.metadata.get("is_follow_up")
            if event.metadata
            else False
        )
        if (
            not task_id
            and not is_follow_up
            and session is not None
        ):
            ctx = AgentCallbackContext(
                agent=agent, session=session,
            )
            state = load_state(ctx)
            if state.task_plan is not None:
                next_task = (
                    state.task_plan.get_next_task()
                )
                if next_task is not None:
                    task_id = next_task.id

        # Fallback to random UUID
        if not task_id:
            task_id = uuid.uuid4().hex

        session_id = (
            session.get_session_id()
            if session
            else "default"
        )

        # Write round_id into metadata BEFORE add_task
        # (add_task does model_copy, so post-add writes
        # would be lost).
        task_metadata = {
            "_handler_round_id": current_round
        }

        try:
            core_task = CoreTask(
                session_id=session_id,
                task_id=task_id,
                task_type=DEEP_TASK_TYPE,
                description=query,
                status=CoreTaskStatus.SUBMITTED,
                metadata=task_metadata,
            )
            if self._task_manager is not None:
                await self._task_manager.add_task(
                    core_task
                )
            else:
                self._resolve_future(
                    {"error": "task_manager is None"},
                    current_round,
                )
                return {"status": "failed"}
        except Exception as e:
            logger.error(
                f"handle_input failed: {e}"
            )
            self._resolve_future(
                {"error": str(e)}, current_round,
            )
            return {
                "status": "failed",
                "error": str(e),
            }

        # Success — do NOT resolve here; wait for
        # completion/failed callback to resolve.
        return {
            "status": "submitted",
            "task_id": task_id,
        }

    async def handle_task_interaction(
        self,
        inputs: EventHandlerInput,
    ) -> Optional[Dict]:
        """Handle a steer event.

        Pushes the steer message into the
        interaction_queues.steering buffer so that
        the executor can inject it before
        the next inner invoke.

        Args:
            inputs: Contains TaskInteractionEvent.

        Returns:
            Acknowledgement dict.
        """
        event = inputs.event
        msg = ""
        if isinstance(event, TaskInteractionEvent):
            if event.interaction:
                first = event.interaction[0]
                msg = getattr(
                    first, "text", str(first)
                )
        if msg and self.interaction_queues is not None:
            self.interaction_queues.push_steer(msg)
        logger.info(f"Steer injected: {msg[:100]}")
        return {
            "status": "steer_injected",
            "msg": msg,
        }

    async def handle_task_completion(
        self,
        inputs: EventHandlerInput,
    ) -> Optional[Dict]:
        """Signal completion to the outer loop.

        Resolves the per-round Future so that
        wait_completion() returns the result.

        Args:
            inputs: Contains TaskCompletionEvent.

        Returns:
            Acknowledgement dict.
        """
        event = inputs.event
        task_id = (
            event.metadata.get("task_id")
            if event.metadata
            else None
        )
        round_id = (
            event.metadata.get(
                "_handler_round_id", self._round_id
            )
            if event.metadata
            else self._round_id
        )

        # Extract result from task_result data
        result: Dict[str, Any] = {}
        task_result = getattr(
            event, "task_result", None
        )
        if task_result:
            for df in task_result:
                data = getattr(df, "data", None)
                if isinstance(data, dict):
                    result = data
                    break
                text = getattr(df, "text", None)
                if text:
                    result["output"] = text

        self._resolve_future(result, round_id)

        return {
            "status": "completed",
            "task_id": task_id,
        }

    async def handle_task_failed(
        self,
        inputs: EventHandlerInput,
    ) -> Optional[Dict]:
        """Signal failure to the outer loop.

        Resolves the per-round Future with an error
        dict so that wait_completion() returns it.

        Args:
            inputs: Contains TaskFailedEvent.

        Returns:
            Acknowledgement dict.
        """
        event = inputs.event
        task_id = (
            event.metadata.get("task_id")
            if event.metadata
            else None
        )
        round_id = (
            event.metadata.get(
                "_handler_round_id", self._round_id
            )
            if event.metadata
            else self._round_id
        )
        error_msg = getattr(
            event, "error_message", "unknown"
        )

        self._resolve_future(
            {"error": str(error_msg)}, round_id,
        )

        return {
            "status": "failed",
            "task_id": task_id,
            "error": str(error_msg),
        }

    async def handle_follow_up(
        self,
        inputs: EventHandlerInput,
    ) -> Optional[Dict]:
        """Handle a follow-up event.

        Pushes the follow-up message into the
        interaction_queues.follow_up buffer so the
        outer task loop can pick it up after the
        current iteration completes.

        Args:
            inputs: Contains FollowUpEvent.

        Returns:
            Acknowledgement dict (must be non-empty
            to avoid MessageQueue ValueError).
        """
        event = inputs.event
        msg = ""
        if hasattr(event, "input_data"):
            for df in event.input_data:
                text = getattr(df, "text", None)
                if text:
                    msg = str(text)
                    break
        if msg and self.interaction_queues is not None:
            self.interaction_queues.push_follow_up(
                msg
            )
        logger.info(
            f"Follow-up queued: {msg[:100]}"
        )
        return {
            "status": "follow_up_queued",
            "msg": msg,
        }

    @staticmethod
    def _extract_query(event: Any) -> str:
        """Pull text from an InputEvent."""
        if isinstance(event, InputEvent):
            for df in event.input_data:
                text = getattr(df, "text", None)
                if text:
                    return str(text)
                data = getattr(df, "data", None)
                if isinstance(data, dict):
                    return str(
                        data.get("query", data)
                    )
        return ""

    async def on_abort(self) -> None:
        """Signal abort to the outer loop.

        Resolves the current round's Future with an
        error dict so wait_completion() returns
        immediately.
        """
        self._resolve_future(
            {"error": "aborted"}, self._round_id,
        )


__all__ = [
    "TaskLoopEventHandler",
]
