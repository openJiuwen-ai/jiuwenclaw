# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from jiuwenswarm.extensions.audit.alert_engine import AlertEngine
from jiuwenswarm.extensions.audit.alert_rules import (
    AlertRule,
    ConsecutiveFailureRule,
    ErrorRateSpikeRule,
    PermissionDenialFloodRule,
    ResponseTimeoutRule,
    TokenBudgetExceededRule,
)
from jiuwenswarm.extensions.audit.config import AuditConfig
from jiuwenswarm.extensions.audit.log_store import LogStore
from jiuwenswarm.extensions.audit.models import (
    Alert,
    AlertSeverity,
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
    timestamp: float,
    session_id: str = "session-1",
    request_id: str | None = None,
    duration_ms: float | None = None,
    token_usage: dict[str, int] | None = None,
    error_type: str | None = None,
    metadata: dict[str, object] | None = None,
) -> AuditEvent:
    return AuditEvent(
        event_id=event_id,
        event_type=event_type,
        timestamp=timestamp,
        session_id=session_id,
        channel_id="channel-1",
        request_id=request_id,
        duration_ms=duration_ms,
        token_usage=token_usage,
        error_type=error_type,
        error_detail="detail" if error_type else None,
        metadata=metadata or {},
    )


async def _persist(store: LogStore, *events: AuditEvent) -> None:
    for event in events:
        await store.write_event(event)


@pytest.mark.asyncio
async def test_consecutive_failure_counts_errors_across_request_markers(store: LogStore) -> None:
    now = time.time()
    events = []
    for index in range(3):
        events.extend([
            _event(
                f"request-{index}",
                AuditEventType.CHAT_REQUEST,
                timestamp=now + index * 2,
                request_id=f"request-{index}",
            ),
            _event(
                f"error-{index}",
                AuditEventType.CHAT_ERROR,
                timestamp=now + index * 2 + 1,
                request_id=f"request-{index}",
                error_type="Failure",
            ),
        ])
    await _persist(store, *events)

    alert = await ConsecutiveFailureRule().evaluate(
        events[-1],
        store,
        AuditConfig(consecutive_failure_threshold=3),
    )

    assert alert is not None
    assert alert.context["consecutive_count"] == 3
    assert alert.context["session_id"] == "session-1"


@pytest.mark.asyncio
async def test_consecutive_failure_is_reset_by_successful_completion(store: LogStore) -> None:
    now = time.time()
    events = [
        _event("old-error", AuditEventType.CHAT_ERROR, timestamp=now, error_type="Failure"),
        _event("success", AuditEventType.CHAT_RESPONSE, timestamp=now + 1),
        _event("request-1", AuditEventType.CHAT_REQUEST, timestamp=now + 2),
        _event("error-1", AuditEventType.CHAT_ERROR, timestamp=now + 3, error_type="Failure"),
        _event("request-2", AuditEventType.CHAT_REQUEST, timestamp=now + 4),
        _event("error-2", AuditEventType.CHAT_ERROR, timestamp=now + 5, error_type="Failure"),
    ]
    await _persist(store, *events)

    alert = await ConsecutiveFailureRule().evaluate(
        events[-1],
        store,
        AuditConfig(consecutive_failure_threshold=3),
    )

    assert alert is None


@pytest.mark.asyncio
async def test_consecutive_failure_ignores_other_sessions(store: LogStore) -> None:
    now = time.time()
    current = _event(
        "current",
        AuditEventType.CHAT_ERROR,
        timestamp=now,
        session_id="target",
        error_type="Failure",
    )
    await store.write_event(current)
    for index in range(5):
        await store.write_event(_event(
            f"other-{index}",
            AuditEventType.CHAT_ERROR,
            timestamp=now + index,
            session_id="other",
            error_type="Failure",
        ))

    alert = await ConsecutiveFailureRule().evaluate(
        current,
        store,
        AuditConfig(consecutive_failure_threshold=2),
    )

    assert alert is None


@pytest.mark.asyncio
async def test_consecutive_failure_only_evaluates_error_with_session(store: LogStore) -> None:
    rule = ConsecutiveFailureRule()
    now = time.time()

    success = _event("success", AuditEventType.CHAT_RESPONSE, timestamp=now)
    no_session = _event(
        "error",
        AuditEventType.CHAT_ERROR,
        timestamp=now,
        session_id="",
        error_type="Failure",
    )

    assert await rule.evaluate(success, store, AuditConfig()) is None
    assert await rule.evaluate(no_session, store, AuditConfig()) is None


@pytest.mark.asyncio
async def test_token_budget_rule_uses_rolling_total(store: LogStore) -> None:
    now = time.time()
    first = _event(
        "tokens-1",
        AuditEventType.CHAT_RESPONSE,
        timestamp=now,
        token_usage={"total_tokens": 60},
    )
    current = _event(
        "tokens-2",
        AuditEventType.CHAT_RESPONSE,
        timestamp=now + 1,
        token_usage={"total_tokens": 50},
    )
    await _persist(store, first, current)

    alert = await TokenBudgetExceededRule().evaluate(
        current,
        store,
        AuditConfig(token_daily_threshold=100),
    )

    assert alert is not None
    assert alert.severity == AlertSeverity.CRITICAL
    assert alert.context["daily_total_tokens"] == 110


@pytest.mark.asyncio
async def test_token_budget_can_be_disabled_and_ignores_empty_usage(store: LogStore) -> None:
    rule = TokenBudgetExceededRule()
    now = time.time()
    event = _event(
        "tokens",
        AuditEventType.CHAT_RESPONSE,
        timestamp=now,
        token_usage={"total_tokens": 5},
    )
    no_usage = _event("none", AuditEventType.CHAT_RESPONSE, timestamp=now)

    assert await rule.evaluate(event, store, AuditConfig(token_daily_threshold=0)) is None
    assert await rule.evaluate(no_usage, store, AuditConfig(token_daily_threshold=1)) is None


@pytest.mark.asyncio
async def test_response_timeout_prefers_measured_duration(store: LogStore) -> None:
    event = _event(
        "response",
        AuditEventType.MEMORY_AFTER_CHAT,
        timestamp=time.time(),
        request_id="request-1",
        duration_ms=2500,
    )

    alert = await ResponseTimeoutRule().evaluate(
        event,
        store,
        AuditConfig(response_timeout_seconds=2),
    )

    assert alert is not None
    assert alert.context["duration_seconds"] == 2.5


@pytest.mark.asyncio
async def test_fast_measured_duration_does_not_fall_back_to_old_request(store: LogStore) -> None:
    now = time.time()
    await store.write_event(_event(
        "unrelated-old-request",
        AuditEventType.CHAT_REQUEST,
        timestamp=now - 100,
        request_id="different-request",
    ))
    event = _event(
        "response",
        AuditEventType.MEMORY_AFTER_CHAT,
        timestamp=now,
        request_id="request-1",
        duration_ms=50,
    )

    alert = await ResponseTimeoutRule().evaluate(
        event,
        store,
        AuditConfig(response_timeout_seconds=2),
    )

    assert alert is None


@pytest.mark.asyncio
async def test_response_timeout_fallback_matches_exact_request_id(store: LogStore) -> None:
    now = time.time()
    matching = _event(
        "matching-request",
        AuditEventType.CHAT_REQUEST,
        timestamp=now - 5,
        request_id="request-1",
    )
    unrelated = _event(
        "unrelated-request",
        AuditEventType.CHAT_REQUEST,
        timestamp=now - 20,
        request_id="request-2",
    )
    await _persist(store, matching, unrelated)
    response = _event(
        "response",
        AuditEventType.MEMORY_AFTER_CHAT,
        timestamp=now,
        request_id="request-1",
    )

    alert = await ResponseTimeoutRule().evaluate(
        response,
        store,
        AuditConfig(response_timeout_seconds=3),
    )

    assert alert is not None
    assert alert.context["request_id"] == "request-1"
    assert alert.context["duration_seconds"] == pytest.approx(5)


@pytest.mark.asyncio
async def test_permission_denial_flood_counts_only_window_events(store: LogStore) -> None:
    now = time.time()
    events = [
        _event(
            f"denied-{index}",
            AuditEventType.CHAT_ERROR,
            timestamp=now + index,
            metadata={"permission_result": "denied"},
        )
        for index in range(3)
    ]
    await _persist(store, *events)

    alert = await PermissionDenialFloodRule().evaluate(
        events[-1],
        store,
        AuditConfig(permission_denial_threshold=3),
    )

    assert alert is not None
    assert alert.context["flood_count"] == 3


@pytest.mark.asyncio
async def test_error_rate_uses_outcomes_not_request_or_memory_markers(store: LogStore) -> None:
    now = time.time()
    events: list[AuditEvent] = []
    for index in range(4):
        events.append(_event(
            f"error-{index}",
            AuditEventType.CHAT_ERROR,
            timestamp=now + index,
            error_type="Failure",
        ))
    events.append(_event("success", AuditEventType.CHAT_RESPONSE, timestamp=now + 5))
    for index in range(10):
        events.append(_event(
            f"marker-{index}",
            AuditEventType.CHAT_REQUEST,
            timestamp=now + 6 + index,
        ))
    await _persist(store, *events)

    alert = await ErrorRateSpikeRule().evaluate(
        events[3],
        store,
        AuditConfig(error_rate_threshold_ratio=0.75),
    )

    assert alert is not None
    assert alert.context["total_in_window"] == 5
    assert alert.context["errors_in_window"] == 4
    assert alert.context["error_rate"] == 0.8


@pytest.mark.asyncio
async def test_error_rate_requires_minimum_sample(store: LogStore) -> None:
    now = time.time()
    events = [
        _event(
            f"error-{index}",
            AuditEventType.CHAT_ERROR,
            timestamp=now + index,
            error_type="Failure",
        )
        for index in range(4)
    ]
    await _persist(store, *events)

    assert await ErrorRateSpikeRule().evaluate(
        events[-1],
        store,
        AuditConfig(error_rate_threshold_ratio=0.1),
    ) is None


class _AlwaysRule(AlertRule):
    name = "always"

    async def evaluate(self, event, store, config) -> Alert | None:
        return Alert(
            alert_type=self.name,
            rule_name=self.name,
            context={"session_id": event.session_id or ""},
        )


class _BrokenRule(AlertRule):
    name = "broken"

    async def evaluate(self, event, store, config) -> Alert | None:
        raise RuntimeError("rule failed")


@pytest.mark.asyncio
async def test_alert_engine_persists_alert_and_isolates_broken_rule(store: LogStore) -> None:
    engine = AlertEngine(
        store,
        [_BrokenRule(), _AlwaysRule()],
        AuditConfig(alert_cooldown_seconds=0),
    )
    event = _event("event", AuditEventType.CHAT_REQUEST, timestamp=time.time())

    alerts = await engine.check_event(event)

    assert len(alerts) == 1
    assert alerts[0].rule_name == "always"
    assert len(await store.query_alerts({})) == 1


@pytest.mark.asyncio
async def test_alert_engine_suppresses_rules_in_memory(store: LogStore) -> None:
    engine = AlertEngine(store, [_AlwaysRule()], AuditConfig())
    event = _event("event", AuditEventType.CHAT_REQUEST, timestamp=time.time())

    engine.suppress_rule("always")
    assert engine.suppressed_rules == frozenset({"always"})
    assert await engine.check_event(event) == []

    engine.unsuppress_rule("always")
    assert engine.rule_names == ("always",)
    assert await engine.check_event(event)


@pytest.mark.asyncio
async def test_alert_engine_deduplicates_same_rule_and_scope(store: LogStore) -> None:
    engine = AlertEngine(
        store,
        [_AlwaysRule()],
        AuditConfig(alert_cooldown_seconds=300),
    )
    first = _event(
        "first",
        AuditEventType.CHAT_REQUEST,
        timestamp=time.time(),
        session_id="same-session",
    )
    second = _event(
        "second",
        AuditEventType.CHAT_REQUEST,
        timestamp=time.time(),
        session_id="same-session",
    )

    assert len(await engine.check_event(first)) == 1
    assert await engine.check_event(second) == []
    assert len(await store.query_alerts({})) == 1


@pytest.mark.asyncio
async def test_alert_engine_allows_same_rule_for_different_scope(store: LogStore) -> None:
    engine = AlertEngine(store, [_AlwaysRule()], AuditConfig(alert_cooldown_seconds=300))
    first = _event(
        "first",
        AuditEventType.CHAT_REQUEST,
        timestamp=time.time(),
        session_id="session-a",
    )
    second = _event(
        "second",
        AuditEventType.CHAT_REQUEST,
        timestamp=time.time(),
        session_id="session-b",
    )

    assert len(await engine.check_event(first)) == 1
    assert len(await engine.check_event(second)) == 1


@pytest.mark.asyncio
async def test_resolved_alert_no_longer_blocks_duplicate(store: LogStore) -> None:
    engine = AlertEngine(store, [_AlwaysRule()], AuditConfig(alert_cooldown_seconds=300))
    event = _event("event", AuditEventType.CHAT_REQUEST, timestamp=time.time())
    first = (await engine.check_event(event))[0]
    await engine.resolve_alert(first.alert_id)

    repeated = await engine.check_event(event)

    assert len(repeated) == 1
