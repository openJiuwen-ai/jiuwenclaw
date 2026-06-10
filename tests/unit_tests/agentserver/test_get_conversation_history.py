# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Integration tests for get_conversation_history method."""

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from jiuwenclaw.agentserver.agent_ws_server import AgentWebSocketServer


def _attach_capture_handler(logger):
    """Project sets jiuwenclaw.* logger propagate=False with custom handlers, preventing caplog.
    Temporarily attach an in-memory handler, returning (records, detach) tuple.
    """
    records: list[logging.LogRecord] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _CaptureHandler(level=logging.DEBUG)
    saved_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    def _detach():
        logger.removeHandler(handler)
        logger.setLevel(saved_level)

    return records, _detach


class TestGetConversationHistory:
    """Tests for AgentWebSocketServer.get_conversation_history."""

    @staticmethod
    def test_valid_session_with_mixed_history(tmp_path):
        """Test valid session with mixed history (retracted and non-retracted content)."""
        session_id = "test-session-001"
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / session_id
        session_dir.mkdir(parents=True)
        history_path = session_dir / "history.json"

        records = [
            {"id": "req1:user", "role": "user", "request_id": "req1", "content": "Hello"},
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.delta",
                "content": "Hi",
            },
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.retract",
                "content": "",
            },
            {"id": "req2:user", "role": "user", "request_id": "req2", "content": "World"},
            {
                "id": "req2:assistant",
                "role": "assistant",
                "request_id": "req2",
                "event_type": "chat.delta",
                "content": "Test",
            },
        ]
        history_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        with patch(
            "jiuwenclaw.agentserver.agent_ws_server.get_agent_sessions_dir",
            return_value=sessions_dir,
        ):
            result = AgentWebSocketServer.get_conversation_history(
                session_id=session_id,
                page_idx=1,
            )

        assert result is not None
        assert result["page_idx"] == 1
        assert result["total_pages"] == 1
        assert len(result["messages"]) == 4
        request_ids = [m["request_id"] for m in result["messages"]]
        assert request_ids == ["req2", "req2", "req1", "req1"]
        assert all("session_id" in m for m in result["messages"])

    @staticmethod
    def test_invalid_session_id_returns_none(tmp_path):
        """Test that invalid session_id returns None."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        with patch(
            "jiuwenclaw.agentserver.agent_ws_server.get_agent_sessions_dir",
            return_value=sessions_dir,
        ):
            assert (
                AgentWebSocketServer.get_conversation_history(session_id="", page_idx=1)
                is None
            )
            assert (
                AgentWebSocketServer.get_conversation_history(
                    session_id="   ",
                    page_idx=1,
                )
                is None
            )
            assert (
                AgentWebSocketServer.get_conversation_history(
                    session_id=None,
                    page_idx=1,
                )
                is None
            )
            assert (
                AgentWebSocketServer.get_conversation_history(
                    session_id=123,
                    page_idx=1,
                )
                is None
            )

    @staticmethod
    def test_invalid_page_idx_returns_none(tmp_path):
        """Test that invalid page_idx returns None."""
        session_id = "test-session"
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "history.json").write_text("[]", encoding="utf-8")

        with patch(
            "jiuwenclaw.agentserver.agent_ws_server.get_agent_sessions_dir",
            return_value=sessions_dir,
        ):
            assert (
                AgentWebSocketServer.get_conversation_history(
                    session_id=session_id,
                    page_idx=0,
                )
                is None
            )
            assert (
                AgentWebSocketServer.get_conversation_history(
                    session_id=session_id,
                    page_idx=-1,
                )
                is None
            )
            assert (
                AgentWebSocketServer.get_conversation_history(
                    session_id=session_id,
                    page_idx=None,
                )
                is None
            )
            assert (
                AgentWebSocketServer.get_conversation_history(
                    session_id=session_id,
                    page_idx="1",
                )
                is None
            )

    @staticmethod
    def test_nonexistent_history_file_returns_none(tmp_path):
        """Test that nonexistent history file returns None."""
        session_id = "nonexistent-session"
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        with patch(
            "jiuwenclaw.agentserver.agent_ws_server.get_agent_sessions_dir",
            return_value=sessions_dir,
        ):
            result = AgentWebSocketServer.get_conversation_history(
                session_id=session_id,
                page_idx=1,
            )
            assert result is None

    @staticmethod
    def test_pagination_edge_cases(tmp_path):
        """Test pagination edge cases."""
        session_id = "test-session-pagination"
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / session_id
        session_dir.mkdir(parents=True)
        history_path = session_dir / "history.json"

        records = [
            {
                "id": f"req{i}:user",
                "role": "user",
                "request_id": f"req{i}",
                "content": f"Message {i}",
            }
            for i in range(100)
        ]
        history_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        with patch(
            "jiuwenclaw.agentserver.agent_ws_server.get_agent_sessions_dir",
            return_value=sessions_dir,
        ):
            result_p1 = AgentWebSocketServer.get_conversation_history(
                session_id=session_id,
                page_idx=1,
            )
            assert result_p1 is not None
            assert result_p1["page_idx"] == 1
            assert result_p1["total_pages"] == 2
            assert len(result_p1["messages"]) == 50

            result_p2 = AgentWebSocketServer.get_conversation_history(
                session_id=session_id,
                page_idx=2,
            )
            assert result_p2 is not None
            assert result_p2["page_idx"] == 2
            assert len(result_p2["messages"]) == 50

            result_p3 = AgentWebSocketServer.get_conversation_history(
                session_id=session_id,
                page_idx=3,
            )
            assert result_p3 is None

    @staticmethod
    def test_page_idx_exceeds_total_pages_returns_none(tmp_path):
        """Test that page_idx exceeding total pages returns None."""
        session_id = "test-session-small"
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / session_id
        session_dir.mkdir(parents=True)
        history_path = session_dir / "history.json"

        records = [
            {
                "id": f"req{i}:user",
                "role": "user",
                "request_id": f"req{i}",
                "content": f"Message {i}",
            }
            for i in range(5)
        ]
        history_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        with patch(
            "jiuwenclaw.agentserver.agent_ws_server.get_agent_sessions_dir",
            return_value=sessions_dir,
        ):
            assert (
                AgentWebSocketServer.get_conversation_history(
                    session_id=session_id,
                    page_idx=2,
                )
                is None
            )

    @staticmethod
    def test_session_id_enrichment(tmp_path):
        """Test that session_id is enriched in messages that lack it."""
        session_id = "test-session-enrich"
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / session_id
        session_dir.mkdir(parents=True)
        history_path = session_dir / "history.json"

        records = [
            {"id": "req1:user", "role": "user", "request_id": "req1", "content": "Hello"},
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.delta",
                "content": "Hi",
                "session_id": "old-session",
            },
        ]
        history_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        with patch(
            "jiuwenclaw.agentserver.agent_ws_server.get_agent_sessions_dir",
            return_value=sessions_dir,
        ):
            result = AgentWebSocketServer.get_conversation_history(
                session_id=session_id,
                page_idx=1,
            )

        assert result is not None
        assert result["messages"][0]["session_id"] == "old-session"
        assert result["messages"][1]["session_id"] == session_id

    @staticmethod
    def test_history_order_is_reversed(tmp_path):
        """Test that history is returned in reverse chronological order."""
        session_id = "test-session-order"
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / session_id
        session_dir.mkdir(parents=True)
        history_path = session_dir / "history.json"

        records = [
            {"id": "req1:user", "role": "user", "request_id": "req1", "content": "First"},
            {"id": "req2:user", "role": "user", "request_id": "req2", "content": "Second"},
            {"id": "req3:user", "role": "user", "request_id": "req3", "content": "Third"},
        ]
        history_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        with patch(
            "jiuwenclaw.agentserver.agent_ws_server.get_agent_sessions_dir",
            return_value=sessions_dir,
        ):
            result = AgentWebSocketServer.get_conversation_history(
                session_id=session_id,
                page_idx=1,
            )

        assert result is not None
        request_ids = [m["request_id"] for m in result["messages"]]
        assert request_ids == ["req3", "req2", "req1"]

    @staticmethod
    def test_malformed_json_returns_empty_result(tmp_path, caplog):
        """Test that malformed JSON in history file returns empty result and logs warning."""
        session_id = "test-session-malformed"
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / session_id
        session_dir.mkdir(parents=True)
        history_path = session_dir / "history.json"

        history_path.write_text("{ not valid json }\n{ also bad }", encoding="utf-8")

        with patch(
            "jiuwenclaw.agentserver.agent_ws_server.get_agent_sessions_dir",
            return_value=sessions_dir,
        ):
            with caplog.at_level(logging.WARNING):
                result = AgentWebSocketServer.get_conversation_history(
                    session_id=session_id,
                    page_idx=1,
                )

        assert result is not None
        assert result["messages"] == []
        assert result["total_pages"] == 1
        assert result["page_idx"] == 1

    @staticmethod
    def test_empty_history_file_returns_valid_result(tmp_path):
        """Test that empty history file returns valid result with no messages."""
        session_id = "test-session-empty"
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / session_id
        session_dir.mkdir(parents=True)
        history_path = session_dir / "history.json"

        history_path.write_text("", encoding="utf-8")

        with patch(
            "jiuwenclaw.agentserver.agent_ws_server.get_agent_sessions_dir",
            return_value=sessions_dir,
        ):
            result = AgentWebSocketServer.get_conversation_history(
                session_id=session_id,
                page_idx=1,
            )

        assert result is not None
        assert result["messages"] == []
        assert result["total_pages"] == 1
        assert result["page_idx"] == 1

    @staticmethod
    def test_session_id_is_stripped(tmp_path):
        """Test that session_id is stripped of whitespace."""
        session_id = "  test-session-strip  "
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / "test-session-strip"
        session_dir.mkdir(parents=True)
        history_path = session_dir / "history.json"

        records = [
            {"id": "req1:user", "role": "user", "request_id": "req1", "content": "Hello"}
        ]
        history_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        with patch(
            "jiuwenclaw.agentserver.agent_ws_server.get_agent_sessions_dir",
            return_value=sessions_dir,
        ):
            result = AgentWebSocketServer.get_conversation_history(
                session_id=session_id,
                page_idx=1,
            )

        assert result is not None
        assert result["messages"][0]["session_id"] == "test-session-strip"

    @staticmethod
    def test_exception_in_read_history_logs_warning(tmp_path):
        """Test that exceptions in read_history_records_for_frontend are logged."""
        session_id = "test-session-error"
        sessions_dir = tmp_path / "sessions"
        session_dir = sessions_dir / session_id
        session_dir.mkdir(parents=True)
        history_path = session_dir / "history.json"
        history_path.write_text("[]", encoding="utf-8")

        from jiuwenclaw.agentserver import agent_ws_server

        records, detach = _attach_capture_handler(agent_ws_server.logger)
        try:
            with patch(
                "jiuwenclaw.agentserver.agent_ws_server.get_agent_sessions_dir",
                return_value=sessions_dir,
            ):
                with patch(
                    "jiuwenclaw.agentserver.agent_ws_server.read_history_records_for_frontend",
                    side_effect=RuntimeError("Test error"),
                ):
                    result = AgentWebSocketServer.get_conversation_history(
                        session_id=session_id,
                        page_idx=1,
                    )

            assert result is None
            warning_records = [r for r in records if r.levelno == logging.WARNING]
            assert len(warning_records) >= 1
            assert "Failed to read history for session" in warning_records[0].getMessage()
            assert session_id in warning_records[0].getMessage()
            assert "Test error" in warning_records[0].getMessage()
        finally:
            detach()