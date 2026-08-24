# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

from __future__ import annotations

import asyncio
import csv
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from jiuwenswarm.extensions.audit.log_store import LogStore
from jiuwenswarm.extensions.audit.models import (
    Alert,
    AlertSeverity,
    AlertStatus,
    AuditEvent,
    AuditEventType,
)


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[LogStore]:
    instance = LogStore(tmp_path / "audit")
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


def _event(
    event_id: str,
    event_type: AuditEventType,
    *,
    timestamp: float | None = None,
    session_id: str = "session-1",
    channel_id: str = "channel-1",
    request_id: str | None = None,
    agent_name: str | None = None,
    duration_ms: float | None = None,
    token_usage: dict[str, int] | None = None,
    error_type: str | None = None,
    metadata: dict[str, object] | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_id=event_id,
        event_type=event_type,
        timestamp=timestamp or time.time(),
        session_id=session_id,
        channel_id=channel_id,
        request_id=request_id,
        agent_name=agent_name,
        duration_ms=duration_ms,
        token_usage=token_usage,
        error_type=error_type,
        metadata=metadata or {},
    )


@pytest.mark.asyncio
async def test_uninitialized_store_fails_fast(tmp_path: Path) -> None:
    instance = LogStore(tmp_path / "audit")

    with pytest.raises(RuntimeError, match="not initialized"):
        await instance.write_event(_event("e1", AuditEventType.CHAT_REQUEST))
    with pytest.raises(RuntimeError, match="not initialized"):
        await instance.query_events({})
    with pytest.raises(RuntimeError, match="not initialized"):
        await instance.query_alerts({})
    with pytest.raises(RuntimeError, match="not initialized"):
        await instance.get_error_summary()

    assert await instance.get_status() == {"status": "not_initialized"}


@pytest.mark.asyncio
async def test_initialize_and_close_are_idempotent(tmp_path: Path) -> None:
    instance = LogStore(tmp_path / "audit")

    await instance.initialize()
    first_connection = instance._db
    await instance.initialize()

    assert instance.initialized is True
    assert instance._db is first_connection

    await instance.close()
    await instance.close()
    assert instance.initialized is False


@pytest.mark.asyncio
async def test_async_context_manager_owns_connection(tmp_path: Path) -> None:
    instance = LogStore(tmp_path / "audit")

    async with instance as opened:
        assert opened is instance
        assert opened.initialized is True

    assert instance.initialized is False


@pytest.mark.asyncio
async def test_write_event_persists_sqlite_and_jsonl(store: LogStore) -> None:
    event = _event(
        "event-1",
        AuditEventType.CHAT_RESPONSE,
        request_id="request-1",
        agent_name="main",
        duration_ms=25,
        token_usage={"total_tokens": 12},
        metadata={"unicode": "审计"},
    )

    await store.write_event(event)

    assert await store.query_events({}) == [event]
    lines = (store.audit_dir / "audit_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    persisted = json.loads(lines[0])
    assert persisted["event_id"] == "event-1"
    assert persisted["metadata"] == {"unicode": "审计"}


@pytest.mark.asyncio
async def test_duplicate_event_id_is_ignored_in_both_stores(store: LogStore) -> None:
    event = _event("duplicate", AuditEventType.CHAT_REQUEST)

    await store.write_event(event)
    await store.write_event(event)

    assert len(await store.query_events({})) == 1
    lines = (store.audit_dir / "audit_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


@pytest.mark.asyncio
async def test_concurrent_writes_keep_valid_jsonl(store: LogStore) -> None:
    events = [
        _event(f"concurrent-{index}", AuditEventType.CHAT_REQUEST)
        for index in range(40)
    ]

    await asyncio.gather(*(store.write_event(event) for event in events))

    queried = await store.query_events({"limit": 100})
    lines = (store.audit_dir / "audit_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(queried) == 40
    assert len(lines) == 40
    assert {json.loads(line)["event_id"] for line in lines} == {
        event.event_id for event in events
    }


@pytest.mark.asyncio
async def test_query_events_supports_all_dimensions(store: LogStore) -> None:
    now = time.time()
    matching = _event(
        "matching",
        AuditEventType.CHAT_ERROR,
        timestamp=now - 5,
        session_id="target-session",
        channel_id="target-channel",
        request_id="target-request",
        agent_name="target-agent",
        error_type="TargetError",
    )
    await store.write_event(matching)
    await store.write_event(_event(
        "other",
        AuditEventType.CHAT_RESPONSE,
        timestamp=now - 2,
        session_id="other-session",
    ))

    filters = {
        "event_type": AuditEventType.CHAT_ERROR,
        "session_id": "target-session",
        "channel_id": "target-channel",
        "request_id": "target-request",
        "agent_name": "target-agent",
        "error_type": "TargetError",
        "start_time": now - 10,
        "end_time": now,
    }

    assert await store.query_events(filters) == [matching]
    assert await store.query_events({"has_error": True}) == [matching]
    assert await store.query_events({"event_types": ["chat_error", "system_stop"]}) == [matching]


@pytest.mark.asyncio
async def test_query_events_orders_and_paginates(store: LogStore) -> None:
    now = time.time()
    for index in range(5):
        await store.write_event(_event(
            f"event-{index}",
            AuditEventType.CHAT_REQUEST,
            timestamp=now + index,
        ))

    page = await store.query_events({"limit": 2, "offset": 1})

    assert [event.event_id for event in page] == ["event-3", "event-2"]


@pytest.mark.asyncio
async def test_query_bounds_invalid_limit_and_offset(store: LogStore) -> None:
    await store.write_event(_event("one", AuditEventType.CHAT_REQUEST))

    assert len(await store.query_events({"limit": "bad", "offset": -3})) == 1
    assert len(await store.query_events({"limit": 0})) == 1


@pytest.mark.asyncio
async def test_write_and_query_alert_lifecycle(store: LogStore) -> None:
    alert = Alert(
        alert_id="alert-1",
        alert_type="timeout",
        severity=AlertSeverity.CRITICAL,
        rule_name="response_timeout",
        message="slow",
        context={"request_id": "request-1"},
    )
    await store.write_alert(alert)

    active = await store.query_alerts({
        "status": "active",
        "severity": "critical",
        "rule_name": "response_timeout",
        "alert_type": "timeout",
    })
    assert active == [alert]

    assert await store.resolve_alert("missing") is False
    assert await store.resolve_alert("alert-1") is True
    resolved = await store.query_alerts({"status": "resolved"})
    assert len(resolved) == 1
    assert resolved[0].status == AlertStatus.RESOLVED
    assert resolved[0].resolved_at is not None


@pytest.mark.asyncio
async def test_suppress_alert_clears_resolved_timestamp(store: LogStore) -> None:
    alert = Alert(alert_id="alert-1", rule_name="rule")
    await store.write_alert(alert)
    await store.resolve_alert(alert.alert_id)

    changed = await store.suppress_alert(alert.alert_id)

    assert changed is True
    result = (await store.query_alerts({"status": "suppressed"}))[0]
    assert result.status == AlertStatus.SUPPRESSED
    assert result.resolved_at is None


@pytest.mark.asyncio
async def test_get_error_summary_uses_correct_column_indexes(store: LogStore) -> None:
    await store.write_event(_event(
        "error-1",
        AuditEventType.CHAT_ERROR,
        error_type="TimeoutError",
    ))
    await store.write_event(_event(
        "error-2",
        AuditEventType.CHAT_ERROR,
        error_type="TimeoutError",
    ))

    summary = await store.get_error_summary(24)

    assert summary == {
        "hours": 24,
        "error_breakdown": [{
            "event_type": "chat_error",
            "error_type": "TimeoutError",
            "count": 2,
        }],
    }


@pytest.mark.asyncio
async def test_token_summary_normalizes_names_and_channels(store: LogStore) -> None:
    await store.write_event(_event(
        "tokens-1",
        AuditEventType.CHAT_RESPONSE,
        channel_id="web",
        token_usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
    ))
    await store.write_event(_event(
        "tokens-2",
        AuditEventType.CHAT_RESPONSE,
        channel_id="web",
        token_usage={"prompt": 7, "completion": 11, "total": 18},
    ))

    summary = await store.get_token_usage_summary(24)

    assert summary["total"] == {
        "prompt_tokens": 9,
        "completion_tokens": 14,
        "total_tokens": 23,
    }
    assert summary["by_channel"]["web"] == {
        "prompt": 9,
        "completion": 14,
        "total": 23,
    }


@pytest.mark.asyncio
async def test_session_summary_uses_correct_indexes_and_latency(store: LogStore) -> None:
    now = time.time()
    await store.write_event(_event(
        "request",
        AuditEventType.CHAT_REQUEST,
        timestamp=now - 10,
        session_id="session-summary",
    ))
    await store.write_event(_event(
        "response",
        AuditEventType.CHAT_RESPONSE,
        timestamp=now,
        session_id="session-summary",
        duration_ms=250,
        token_usage={"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
    ))
    await store.write_event(_event(
        "error",
        AuditEventType.CHAT_ERROR,
        timestamp=now - 5,
        session_id="session-summary",
        duration_ms=500,
        error_type="Error",
    ))

    summary = await store.get_session_summary("session-summary")

    assert summary is not None
    assert summary["event_counts"] == {
        "chat_error": 1,
        "chat_request": 1,
        "chat_response": 1,
    }
    assert summary["total_requests"] == 1
    assert summary["total_errors"] == 1
    assert summary["total_tokens"] == {"prompt": 4, "completion": 6, "total": 10}
    assert summary["duration_seconds"] == pytest.approx(10)
    assert summary["average_duration_ms"] == pytest.approx(375)
    assert summary["max_duration_ms"] == 500
    assert await store.get_session_summary("missing") is None


@pytest.mark.asyncio
async def test_list_sessions_returns_compact_recent_statistics(store: LogStore) -> None:
    now = time.time()
    for session_id, timestamp in (("older", now - 20), ("newer", now - 10)):
        await store.write_event(_event(
            f"{session_id}-request",
            AuditEventType.CHAT_REQUEST,
            timestamp=timestamp,
            session_id=session_id,
        ))
        await store.write_event(_event(
            f"{session_id}-response",
            AuditEventType.CHAT_RESPONSE,
            timestamp=timestamp + 2,
            session_id=session_id,
            duration_ms=100,
        ))

    sessions = await store.list_sessions(hours=1, limit=1)

    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "newer"
    assert sessions[0]["total_events"] == 2
    assert sessions[0]["total_requests"] == 1
    assert sessions[0]["duration_seconds"] == pytest.approx(2)


@pytest.mark.asyncio
async def test_overview_aggregates_events_alerts_tokens_and_latency(store: LogStore) -> None:
    await store.write_event(_event("request", AuditEventType.CHAT_REQUEST))
    await store.write_event(_event(
        "error",
        AuditEventType.CHAT_ERROR,
        duration_ms=400,
        token_usage={"total_tokens": 8},
        error_type="Failure",
    ))
    await store.write_alert(Alert(
        alert_id="alert",
        severity=AlertSeverity.WARNING,
        rule_name="failure",
    ))

    overview = await store.get_overview(24)

    assert overview["total_events"] == 2
    assert overview["total_sessions"] == 1
    assert overview["total_errors"] == 1
    assert overview["error_event_ratio"] == 0.5
    assert overview["average_duration_ms"] == 400
    assert overview["event_counts"] == {"chat_error": 1, "chat_request": 1}
    assert overview["alerts"] == {"active": {"warning": 1}}
    assert overview["token_usage"]["total_tokens"] == 8


@pytest.mark.asyncio
async def test_event_timeseries_groups_chronologically(store: LogStore) -> None:
    bucket_seconds = 60 * 60
    current_bucket = int(time.time() // bucket_seconds) * bucket_seconds
    await store.write_event(_event(
        "old-request",
        AuditEventType.CHAT_REQUEST,
        timestamp=current_bucket - 30,
    ))
    await store.write_event(_event(
        "request",
        AuditEventType.CHAT_REQUEST,
        timestamp=current_bucket + 10,
    ))
    await store.write_event(_event(
        "response",
        AuditEventType.CHAT_RESPONSE,
        timestamp=current_bucket + 20,
        duration_ms=100,
    ))
    await store.write_event(_event(
        "error",
        AuditEventType.CHAT_ERROR,
        timestamp=current_bucket + 30,
        duration_ms=300,
        error_type="Failure",
    ))

    timeline = await store.get_event_timeseries(hours=2, bucket_minutes=60)

    assert len(timeline) == 2
    assert timeline[0]["bucket_start"] == current_bucket - bucket_seconds
    assert timeline[0]["total_events"] == 1
    assert timeline[1]["bucket_start"] == current_bucket
    assert timeline[1]["total_events"] == 3
    assert timeline[1]["total_requests"] == 1
    assert timeline[1]["total_responses"] == 1
    assert timeline[1]["total_errors"] == 1
    assert timeline[1]["error_event_ratio"] == pytest.approx(1 / 3)
    assert timeline[1]["average_duration_ms"] == 200
    assert timeline[1]["max_duration_ms"] == 300


@pytest.mark.asyncio
async def test_event_timeseries_clamps_bucket_width(store: LogStore) -> None:
    await store.write_event(_event("event", AuditEventType.CHAT_REQUEST))

    too_small = await store.get_event_timeseries(bucket_minutes=0)
    too_large = await store.get_event_timeseries(bucket_minutes=100_000)

    assert too_small[0]["bucket_minutes"] == 1
    assert too_large[0]["bucket_minutes"] == 1440


@pytest.mark.asyncio
async def test_cleanup_counts_each_deleted_row_once_and_filters_jsonl(store: LogStore) -> None:
    old = time.time() - 10 * 86400
    await store.write_event(_event("old-event", AuditEventType.CHAT_REQUEST, timestamp=old))
    await store.write_event(_event("new-event", AuditEventType.CHAT_REQUEST))
    alert = Alert(
        alert_id="old-alert",
        status=AlertStatus.RESOLVED,
        triggered_at=old,
        resolved_at=old + 1,
        rule_name="old",
    )
    await store.write_alert(alert)

    deleted = await store.cleanup_old_events(5)

    assert deleted == 2
    assert [event.event_id for event in await store.query_events({})] == ["new-event"]
    assert await store.query_alerts({}) == []
    event_lines = (store.audit_dir / "audit_events.jsonl").read_text(encoding="utf-8").splitlines()
    alert_lines = (store.audit_dir / "audit_alerts.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["event_id"] for line in event_lines] == ["new-event"]
    assert alert_lines == []


@pytest.mark.asyncio
async def test_cleanup_rejects_negative_retention(store: LogStore) -> None:
    with pytest.raises(ValueError, match="retention_days"):
        await store.cleanup_old_events(-1)


@pytest.mark.asyncio
async def test_exports_jsonl_and_csv(store: LogStore, tmp_path: Path) -> None:
    await store.write_event(_event(
        "exported",
        AuditEventType.CHAT_RESPONSE,
        metadata={"text": "中文"},
        token_usage={"total_tokens": 3},
    ))
    jsonl_path = tmp_path / "exports" / "events.jsonl"
    csv_path = tmp_path / "exports" / "events.csv"

    jsonl_count = await store.export_to_jsonl(jsonl_path, {})
    csv_count = await store.export_to_csv(csv_path, {})

    assert jsonl_count == 1
    assert json.loads(jsonl_path.read_text(encoding="utf-8"))["event_id"] == "exported"
    assert csv_count == 1
    with open(csv_path, encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows[0]["event_id"] == "exported"
    assert json.loads(rows[0]["metadata"]) == {"text": "中文"}


@pytest.mark.asyncio
async def test_get_status_reports_counts_and_last_event(store: LogStore) -> None:
    event = _event("status-event", AuditEventType.SYSTEM_START)
    await store.write_event(event)
    await store.write_alert(Alert(alert_id="status-alert", rule_name="rule"))

    status = await store.get_status()

    assert status["status"] == "running"
    assert status["audit_dir"] == str(store.audit_dir)
    assert status["total_events"] == 1
    assert status["active_alerts"] == 1
    assert status["last_event_timestamp"] == event.timestamp
