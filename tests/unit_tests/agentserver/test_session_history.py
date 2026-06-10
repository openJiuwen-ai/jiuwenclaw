# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Tests for session_history filtering functions."""

import json
import tempfile
from pathlib import Path

from jiuwenclaw.agentserver.session_history import read_history_records_for_frontend


def test_empty_history_file() -> None:
    """Test filtering empty history file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "history.json"
        path.write_text("", encoding="utf-8")
        result = read_history_records_for_frontend(path)
        assert result == []


def test_nonexistent_history_file() -> None:
    """Test filtering when history file does not exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "history.json"
        result = read_history_records_for_frontend(path)
        assert result == []


def test_single_retract_filters_matching_events() -> None:
    """Test that a retract filters matching delta/reasoning/tool events."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "history.json"
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
                "event_type": "chat.reasoning",
                "content": "Thinking...",
            },
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.retract",
                "content": "",
            },
            {"id": "req2:user", "role": "user", "request_id": "req2", "content": "World"},
        ]
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        result = read_history_records_for_frontend(path)

        assert len(result) == 3
        request_ids = [r["request_id"] for r in result]
        event_types = [r.get("event_type") for r in result]

        assert request_ids == ["req1", "req1", "req2"]
        assert event_types == [None, "chat.retract", None]


def test_multiple_retracts() -> None:
    """Test that multiple retracts each filter their own events."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "history.json"
        records = [
            {"id": "req1:user", "role": "user", "request_id": "req1", "content": "Q1"},
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.delta",
                "content": "A1",
            },
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.retract",
                "content": "",
            },
            {"id": "req2:user", "role": "user", "request_id": "req2", "content": "Q2"},
            {
                "id": "req2:assistant",
                "role": "assistant",
                "request_id": "req2",
                "event_type": "chat.delta",
                "content": "A2",
            },
            {
                "id": "req2:assistant",
                "role": "assistant",
                "request_id": "req2",
                "event_type": "chat.retract",
                "content": "",
            },
            {"id": "req3:user", "role": "user", "request_id": "req3", "content": "Q3"},
            {
                "id": "req3:assistant",
                "role": "assistant",
                "request_id": "req3",
                "event_type": "chat.delta",
                "content": "A3",
            },
        ]
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        result = read_history_records_for_frontend(path)

        assert len(result) == 6
        request_ids = [r["request_id"] for r in result]
        event_types = [r.get("event_type") for r in result]

        assert request_ids == ["req1", "req1", "req2", "req2", "req3", "req3"]
        assert event_types == [None, "chat.retract", None, "chat.retract", None, "chat.delta"]


def test_mixed_event_types() -> None:
    """Test that only specific event types are filtered."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "history.json"
        records = [
            {"id": "req1:user", "role": "user", "request_id": "req1", "content": "Q"},
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.delta",
                "content": "D",
            },
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.reasoning",
                "content": "R",
            },
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.tool_call",
                "content": "TC",
            },
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.tool_result",
                "content": "TR",
            },
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.other",
                "content": "O",
            },
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.retract",
                "content": "",
            },
        ]
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        result = read_history_records_for_frontend(path)

        assert len(result) == 3
        event_types = [r.get("event_type") for r in result]

        assert event_types == [None, "chat.other", "chat.retract"]


def test_malformed_retract_missing_request_id() -> None:
    """Test that malformed retract with missing request_id doesn't break filtering."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "history.json"
        records = [
            {"id": "req1:user", "role": "user", "request_id": "req1", "content": "Q"},
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.delta",
                "content": "D",
            },
            {
                "id": "bad:assistant",
                "role": "assistant",
                "event_type": "chat.retract",
                "content": "",
            },
            {"id": "req2:user", "role": "user", "request_id": "req2", "content": "Q2"},
            {
                "id": "req2:assistant",
                "role": "assistant",
                "request_id": "req2",
                "event_type": "chat.delta",
                "content": "D2",
            },
        ]
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        result = read_history_records_for_frontend(path)

        assert len(result) == 5
        request_ids = [r.get("request_id") for r in result]
        assert "req1" in request_ids
        assert "req2" in request_ids


def test_retract_event_itself_preserved() -> None:
    """Test that retract events themselves are preserved."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "history.json"
        records = [
            {"id": "req1:user", "role": "user", "request_id": "req1", "content": "Q"},
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.delta",
                "content": "D",
            },
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.retract",
                "content": "",
            },
        ]
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        result = read_history_records_for_frontend(path)

        assert len(result) == 2
        retract_events = [r for r in result if r.get("event_type") == "chat.retract"]
        assert len(retract_events) == 1


def test_no_retract_preserves_all() -> None:
    """Test that without any retract, all events are preserved."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "history.json"
        records = [
            {"id": "req1:user", "role": "user", "request_id": "req1", "content": "Q"},
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.delta",
                "content": "D",
            },
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.reasoning",
                "content": "R",
            },
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.tool_call",
                "content": "TC",
            },
        ]
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        result = read_history_records_for_frontend(path)

        assert len(result) == 4


def test_different_request_ids_not_affected() -> None:
    """Test that retract only affects events with matching request_id."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "history.json"
        records = [
            {"id": "req1:user", "role": "user", "request_id": "req1", "content": "Q1"},
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.delta",
                "content": "D1",
            },
            {
                "id": "req1:assistant",
                "role": "assistant",
                "request_id": "req1",
                "event_type": "chat.retract",
                "content": "",
            },
            {"id": "req2:user", "role": "user", "request_id": "req2", "content": "Q2"},
            {
                "id": "req2:assistant",
                "role": "assistant",
                "request_id": "req2",
                "event_type": "chat.delta",
                "content": "D2",
            },
        ]
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        result = read_history_records_for_frontend(path)

        assert len(result) == 4

        req2_events = [r for r in result if r.get("request_id") == "req2"]
        assert len(req2_events) == 2
        assert req2_events[1].get("event_type") == "chat.delta"


def test_none_request_id_not_added_to_revoked_rids() -> None:
    """Test that None request_id from malformed retract doesn't pollute revoked_rids."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "history.json"
        records = [
            {"id": "req1:user", "role": "user", "request_id": "req1", "content": "Q"},
            {
                "id": "bad:assistant",
                "role": "assistant",
                "event_type": "chat.retract",
                "content": "",
            },
            {"id": "req2:user", "role": "user", "content": "Q2"},
            {
                "id": "req2:assistant",
                "role": "assistant",
                "event_type": "chat.delta",
                "content": "D2",
            },
        ]
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

        result = read_history_records_for_frontend(path)

        delta_events = [r for r in result if r.get("event_type") == "chat.delta"]
        assert len(delta_events) == 1