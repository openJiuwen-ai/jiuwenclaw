# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression coverage for the existing PPT outline interaction behavior."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.agentserver.tools import ask_user_question_tool as ask_tool


@pytest.mark.asyncio
async def test_noninteractive_outline_preview_still_prompts(monkeypatch):
    """Disabling guided mode must not skip the existing PPT outline prompt."""
    registry = MagicMock()
    registry.stream_interactive_ask_enabled.return_value = False
    registry.wait_for_answer = AsyncMock(
        return_value=[
            {"question_index": 0, "selected_option_ids": ["outline_confirm"]}
        ]
    )
    server = MagicMock()
    server.send_push = AsyncMock()

    monkeypatch.setattr(
        ask_tool,
        "get_ask_request_context",
        lambda: (False, "session-1", "stream-1", "channel-1"),
    )
    monkeypatch.setattr(
        ask_tool.AskUserQuestionRegistry,
        "get_instance",
        lambda: registry,
    )
    monkeypatch.setattr(
        ask_tool.AgentWebSocketServer,
        "get_instance",
        lambda: server,
    )

    result = await ask_tool._ask_user_question_impl(
        [
            {
                "question": "请确认大纲",
                "preview": {"text": "# PPT 大纲", "format": "markdown"},
                "options": [{"id": "outline_confirm", "label": "确认大纲"}],
            }
        ]
    )

    assert result["status"] == "answered"
    server.send_push.assert_awaited_once()
    registry.wait_for_answer.assert_awaited_once()
