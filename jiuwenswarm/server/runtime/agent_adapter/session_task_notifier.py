# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bridge agent-core session task notifications to swarm push events."""

from __future__ import annotations

import logging
import time
from typing import Any

from openjiuwen.harness.tools.subagent.session_notify import SessionTaskNotification

logger = logging.getLogger(__name__)


class SwarmSessionTaskNotifier:
    """Push chat.session_result (+ optional chat.final summary) on task done."""

    async def notify_session_task_done(
        self,
        notification: SessionTaskNotification,
    ) -> None:
        """Push session_result card and, when text exists, a chat.final bubble.

        ``payload.session_id`` MUST be ``notification.parent_session_id``.
        """
        await self._push_session_result(notification)

        visible = (
            notification.result
            if notification.status == "completed"
            else notification.error
        ) or ""
        if visible:
            await self._push_task_summary(notification, visible)

    async def _push_session_result(
        self,
        notification: SessionTaskNotification,
    ) -> None:
        from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

        if notification.status == "completed":
            payload_status = "completed"
            result = notification.result
        elif notification.status == "canceled":
            payload_status = "canceled"
            result = notification.error or "canceled"
        else:
            payload_status = "error"
            result = notification.error or ""

        payload: dict[str, Any] = {
            "event_type": "chat.session_result",
            "session_id": notification.parent_session_id,
            "description": notification.description,
            "status": payload_status,
            "result": result or "",
            "subagent_id": notification.subagent_id,
            "subagent_type": notification.subagent_type,
            "sub_session_id": notification.sub_session_id,
            "task_id": notification.task_id,
            "is_resume": notification.is_resume,
        }

        msg = {
            "request_id": notification.request_id,
            "channel_id": notification.channel_id,
            "session_id": notification.parent_session_id,
            "payload": payload,
            "is_complete": False,
        }

        logger.info(
            "[SwarmSessionTaskNotifier] pushing chat.session_result: "
            "parent_session_id=%s subagent_id=%s task_id=%s status=%s is_resume=%s",
            notification.parent_session_id,
            notification.subagent_id,
            notification.task_id,
            payload_status,
            notification.is_resume,
        )

        self._persist_history(notification, payload)
        await AgentWebSocketServer.get_instance().send_push(msg)

    async def _push_task_summary(
        self,
        notification: SessionTaskNotification,
        content: str,
    ) -> None:
        """Push+persist the task result as a normal ``chat.final`` bubble."""
        from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

        payload: dict[str, Any] = {
            "event_type": "chat.final",
            "session_id": notification.parent_session_id,
            "content": content,
            "source": "session_task_summary",
            "task_id": notification.task_id,
            "subagent_id": notification.subagent_id,
        }
        msg = {
            "request_id": notification.request_id,
            "channel_id": notification.channel_id,
            "session_id": notification.parent_session_id,
            "payload": payload,
            "is_complete": False,
        }

        logger.info(
            "[SwarmSessionTaskNotifier] pushing session_task_summary as "
            "chat.final: parent_session_id=%s subagent_id=%s task_id=%s",
            notification.parent_session_id,
            notification.subagent_id,
            notification.task_id,
        )

        try:
            from jiuwenswarm.server.runtime.session.session_history import (
                append_history_record,
            )

            append_history_record(
                session_id=notification.parent_session_id,
                request_id=f"{notification.task_id or notification.request_id}-summary",
                channel_id=notification.channel_id,
                role="assistant",
                event_type="chat.final",
                content=content,
                timestamp=time.time(),
                extra={
                    "source": "session_task_summary",
                    "task_id": notification.task_id,
                    "subagent_id": notification.subagent_id,
                },
            )
        except Exception:
            logger.exception(
                "[SwarmSessionTaskNotifier] failed to persist task summary to "
                "history: parent_session_id=%s task_id=%s",
                notification.parent_session_id,
                notification.task_id,
            )

        await AgentWebSocketServer.get_instance().send_push(msg)

    @staticmethod
    def _persist_history(
        notification: SessionTaskNotification,
        payload: dict[str, Any],
    ) -> None:
        """Persist chat.session_result into parent session history.jsonl."""
        try:
            from jiuwenswarm.server.runtime.session.session_history import (
                append_history_record,
            )

            append_history_record(
                session_id=notification.parent_session_id,
                request_id=notification.task_id or notification.request_id,
                channel_id=notification.channel_id,
                role="assistant",
                event_type="chat.session_result",
                content="",
                timestamp=time.time(),
                extra=dict(payload),
            )
        except Exception:
            logger.exception(
                "[SwarmSessionTaskNotifier] failed to persist chat.session_result to history:"
                " parent_session_id=%s task_id=%s",
                notification.parent_session_id,
                notification.task_id,
            )


__all__ = ["SwarmSessionTaskNotifier"]
