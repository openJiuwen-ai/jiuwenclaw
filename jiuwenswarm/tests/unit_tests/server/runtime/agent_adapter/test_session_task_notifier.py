# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for SwarmSessionTaskNotifier.

Covers conversion from agent-core's SessionTaskNotification into
chat.session_result + session_task_summary chat.final, routed through
AgentWebSocketServer.send_push with the parent (not sub) session id.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.harness.tools.subagent.session_notify import SessionTaskNotification

from jiuwenswarm.server.runtime.agent_adapter.session_task_notifier import (
    SwarmSessionTaskNotifier,
)


def _make_notification(**overrides) -> SessionTaskNotification:
    base = dict(
        task_id="task-1",
        subagent_id="subagent-1",
        subagent_type="general-purpose",
        sub_session_id="parent-session_sub_abcd1234",
        parent_session_id="parent-session",
        description="do something useful",
        status="completed",
        result="the final answer",
        error="",
        request_id="req-1",
        channel_id="web",
        is_resume=False,
    )
    base.update(overrides)
    return SessionTaskNotification(**base)


def _payloads(mock_server: AsyncMock) -> list[dict]:
    return [call.args[0]["payload"] for call in mock_server.send_push.await_args_list]


@pytest.mark.asyncio
class TestSwarmSessionTaskNotifier:
    async def test_completed_notification_pushes_session_result_and_summary(self) -> None:
        notifier = SwarmSessionTaskNotifier()
        notification = _make_notification()

        mock_server = AsyncMock()
        with patch(
            "jiuwenswarm.server.agent_ws_server.AgentWebSocketServer.get_instance",
            return_value=mock_server,
        ):
            await notifier.notify_session_task_done(notification)

        assert mock_server.send_push.await_count == 2
        session_result, summary = _payloads(mock_server)

        assert session_result["event_type"] == "chat.session_result"
        assert session_result["status"] == "completed"
        assert session_result["result"] == "the final answer"

        assert summary["event_type"] == "chat.final"
        assert summary["source"] == "session_task_summary"
        assert summary["content"] == "the final answer"
        assert summary["task_id"] == "task-1"
        assert summary["subagent_id"] == "subagent-1"

    async def test_error_notification_becomes_error_status(self) -> None:
        notifier = SwarmSessionTaskNotifier()
        notification = _make_notification(
            status="error", result="", error="boom", is_resume=True
        )

        mock_server = AsyncMock()
        with patch(
            "jiuwenswarm.server.agent_ws_server.AgentWebSocketServer.get_instance",
            return_value=mock_server,
        ):
            await notifier.notify_session_task_done(notification)

        session_result, summary = _payloads(mock_server)
        assert session_result["status"] == "error"
        assert session_result["result"] == "boom"
        assert session_result["is_resume"] is True
        assert summary["content"] == "boom"

    async def test_payload_session_id_uses_parent_session_id(self) -> None:
        notifier = SwarmSessionTaskNotifier()
        notification = _make_notification(
            parent_session_id="parent-session",
            sub_session_id="parent-session_sub_zzzz",
        )

        mock_server = AsyncMock()
        with patch(
            "jiuwenswarm.server.agent_ws_server.AgentWebSocketServer.get_instance",
            return_value=mock_server,
        ):
            await notifier.notify_session_task_done(notification)

        for payload in _payloads(mock_server):
            assert payload["session_id"] == "parent-session"
            assert payload["session_id"] != notification.sub_session_id

    async def test_msg_session_id_uses_parent_session_id(self) -> None:
        notifier = SwarmSessionTaskNotifier()
        notification = _make_notification(parent_session_id="parent-session")

        mock_server = AsyncMock()
        with patch(
            "jiuwenswarm.server.agent_ws_server.AgentWebSocketServer.get_instance",
            return_value=mock_server,
        ):
            await notifier.notify_session_task_done(notification)

        for call in mock_server.send_push.await_args_list:
            msg = call.args[0]
            assert msg["session_id"] == "parent-session"
            assert msg["request_id"] == "req-1"
            assert msg["channel_id"] == "web"

    async def test_payload_passes_through_subagent_fields(self) -> None:
        notifier = SwarmSessionTaskNotifier()
        notification = _make_notification(
            subagent_id="subagent-42",
            sub_session_id="parent-session_sub_dead",
            task_id="task-99",
            is_resume=True,
        )

        mock_server = AsyncMock()
        with patch(
            "jiuwenswarm.server.agent_ws_server.AgentWebSocketServer.get_instance",
            return_value=mock_server,
        ):
            await notifier.notify_session_task_done(notification)

        session_result = _payloads(mock_server)[0]
        assert session_result["subagent_id"] == "subagent-42"
        assert session_result["sub_session_id"] == "parent-session_sub_dead"
        assert session_result["task_id"] == "task-99"
        assert session_result["is_resume"] is True

    async def test_empty_result_skips_summary_bubble(self) -> None:
        """No result/error → session_result only (no empty chat.final)."""
        notifier = SwarmSessionTaskNotifier()
        notification = _make_notification(result="", error="")

        mock_server = AsyncMock()
        with patch(
            "jiuwenswarm.server.agent_ws_server.AgentWebSocketServer.get_instance",
            return_value=mock_server,
        ):
            await notifier.notify_session_task_done(notification)

        mock_server.send_push.assert_awaited_once()
        payload = mock_server.send_push.await_args.args[0]["payload"]
        assert payload["event_type"] == "chat.session_result"

    async def test_canceled_notification_pushes_canceled_status_and_summary(self) -> None:
        notifier = SwarmSessionTaskNotifier()
        notification = _make_notification(
            status="canceled", result="", error="任务 tid 已取消"
        )

        mock_server = AsyncMock()
        with patch(
            "jiuwenswarm.server.agent_ws_server.AgentWebSocketServer.get_instance",
            return_value=mock_server,
        ):
            await notifier.notify_session_task_done(notification)

        session_result, summary = _payloads(mock_server)
        assert session_result["status"] == "canceled"
        assert session_result["result"] == "任务 tid 已取消"
        assert "is_parallel" not in session_result
        assert summary["content"] == "任务 tid 已取消"
        assert summary["source"] == "session_task_summary"
