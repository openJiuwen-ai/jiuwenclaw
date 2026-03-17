# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""TaskLoopController — Controller subclass for round-based loops.

Encapsulates round management (prepare/wait/complete),
follow-up queue operations, and loop exit logic that
are specific to the DeepAgent outer task loop.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from openjiuwen.core.controller.base import Controller
from openjiuwen.core.controller.schema.event import InputEvent
from openjiuwen.core.session.agent import Session
from openjiuwen.deepagents.task_loop.loop_queues import (
    LoopQueues,
)


class TaskLoopController(Controller):
    """Controller subclass with round-based loop support.

    Encapsulates round management (prepare/wait/complete),
    follow-up queue operations, and loop exit logic that
    are specific to the DeepAgent outer task loop.
    """

    def __init__(self) -> None:
        super().__init__()

    def _get_interaction_queues(
        self,
    ) -> Optional[LoopQueues]:
        """Get interaction queues from DeepAgent handler.

        TaskLoopController is intended to be paired with
        TaskLoopEventHandler, but we keep this defensive
        in case a different handler is configured.
        """
        handler = self._event_handler
        if handler is None:
            return None
        return getattr(handler, "interaction_queues", None)

    async def submit_round(
        self,
        session: Session,
        query: str,
        is_follow_up: bool = False,
    ) -> None:
        """Prepare a round, build InputEvent, publish it.

        Encapsulates: handler.prepare_round() + event
        construction + metadata injection + publish.

        Args:
            session: Current session.
            query: User query text.
            is_follow_up: Whether this round is a
                follow-up continuation.
        """
        handler = self._event_handler
        round_id = handler.prepare_round()

        event = InputEvent.from_user_input(query)
        event.metadata = event.metadata or {}
        event.metadata["_handler_round_id"] = round_id
        if is_follow_up:
            event.metadata["is_follow_up"] = True

        await self.publish_event_async(session, event)

    async def wait_round_completion(
        self,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Wait for the current round to complete.

        Delegates to event_handler.wait_completion().

        Args:
            timeout: Max seconds to wait. None means
                no limit.

        Returns:
            Result dict from the round.
        """
        return await self._event_handler.wait_completion(
            timeout=timeout,
        )

    def drain_follow_up(self) -> List[str]:
        """Drain follow-up messages from handler queues.

        Returns:
            List of follow-up message strings.
        """
        queues = self._get_interaction_queues()
        if queues is not None:
            return queues.drain_follow_up()
        return []

    def has_follow_up(self) -> bool:
        """Check if follow-up messages are pending.

        Returns:
            True if there are pending follow-up messages.
        """
        queues = self._get_interaction_queues()
        if queues is not None:
            return queues.has_follow_up()
        return False


__all__ = ["TaskLoopController"]
