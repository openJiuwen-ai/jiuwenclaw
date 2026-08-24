# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

from __future__ import annotations

import time

import pytest

from jiuwenswarm.extensions.audit.config import AuditConfig, _bool_val, load_audit_config
from jiuwenswarm.extensions.audit.models import (
    Alert,
    AlertSeverity,
    AlertStatus,
    AuditEvent,
    AuditEventType,
)


def test_audit_event_generates_identity_and_timestamp() -> None:
    before = time.time()

    event = AuditEvent(event_type=AuditEventType.CHAT_REQUEST)

    assert event.event_id.startswith("audit_")
    assert len(event.event_id) == len("audit_") + 12
    assert event.timestamp >= before
    assert event.timestamp <= time.time()


def test_audit_event_round_trip_preserves_all_fields() -> None:
    original = AuditEvent(
        event_id="audit_fixed",
        event_type=AuditEventType.CHAT_ERROR,
        timestamp=123.5,
        session_id="session-1",
        channel_id="channel-1",
        request_id="request-1",
        agent_name="agent-1",
        duration_ms=42.25,
        token_usage={"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        error_type="TimeoutError",
        error_detail="request timed out",
        metadata={"retryable": True},
    )

    restored = AuditEvent.from_dict(original.to_dict())

    assert restored == original
    assert restored.is_error is True
    assert restored.total_tokens == 7


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (None, 0),
        ({}, 0),
        ({"total": 9}, 9),
        ({"total_tokens": "11"}, 11),
        ({"total_tokens": "invalid"}, 0),
    ],
)
def test_audit_event_normalizes_total_tokens(
    usage: dict[str, object] | None,
    expected: int,
) -> None:
    event = AuditEvent(token_usage=usage)

    assert event.total_tokens == expected


def test_non_error_event_with_error_type_is_considered_error() -> None:
    event = AuditEvent(
        event_type=AuditEventType.SYSTEM_STOP,
        error_type="ShutdownFailure",
    )

    assert event.is_error is True


@pytest.mark.parametrize("bad_timestamp", [None, "", "not-a-number", object()])
def test_audit_event_from_dict_tolerates_bad_timestamp(bad_timestamp: object) -> None:
    event = AuditEvent.from_dict({"timestamp": bad_timestamp})

    assert isinstance(event.timestamp, float)
    assert event.timestamp > 0


def test_audit_event_from_dict_tolerates_legacy_values() -> None:
    event = AuditEvent.from_dict({
        "event_type": "future_event_type",
        "duration_ms": "bad",
        "token_usage": [],
        "metadata": None,
    })

    assert event.event_type == AuditEventType.SYSTEM_START
    assert event.duration_ms is None
    assert event.token_usage is None
    assert event.metadata == {}


def test_audit_event_from_dict_copies_mutable_mappings() -> None:
    token_usage = {"total_tokens": 1}
    metadata = {"source": "test"}

    event = AuditEvent.from_dict({
        "token_usage": token_usage,
        "metadata": metadata,
    })
    token_usage["total_tokens"] = 99
    metadata["source"] = "changed"

    assert event.token_usage == {"total_tokens": 1}
    assert event.metadata == {"source": "test"}


def test_alert_round_trip_preserves_all_fields() -> None:
    alert = Alert(
        alert_id="audit_alert",
        alert_type="timeout",
        severity=AlertSeverity.CRITICAL,
        status=AlertStatus.RESOLVED,
        triggered_at=100.0,
        rule_name="response_timeout",
        message="slow response",
        context={"request_id": "req-1"},
        resolved_at=120.0,
    )

    assert Alert.from_dict(alert.to_dict()) == alert


def test_alert_from_dict_tolerates_invalid_persisted_values() -> None:
    alert = Alert.from_dict({
        "severity": "emergency",
        "status": "unknown",
        "triggered_at": None,
        "resolved_at": "bad",
        "context": [],
    })

    assert alert.severity == AlertSeverity.WARNING
    assert alert.status == AlertStatus.ACTIVE
    assert alert.triggered_at > 0
    assert alert.resolved_at is None
    assert alert.context == {}


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "On"])
def test_bool_val_recognizes_truthy_strings(value: str) -> None:
    assert _bool_val(value, default=False) is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", " no ", "Off"])
def test_bool_val_recognizes_falsy_strings(value: str) -> None:
    assert _bool_val(value, default=True) is False


def test_bool_val_uses_default_for_unknown_string() -> None:
    assert _bool_val("sometimes", default=True) is True
    assert _bool_val("sometimes", default=False) is False


def test_load_audit_config_accepts_explicit_source() -> None:
    config = load_audit_config({
        "audit": {
            "enabled": "off",
            "audit_dir": " ./var/audit ",
            "retention_days": "14",
            "consecutive_failure_threshold": "4",
            "token_daily_threshold": "9000",
            "response_timeout_seconds": "3.5",
            "permission_denial_window_minutes": "8",
            "permission_denial_threshold": "6",
            "error_rate_window_minutes": "12",
            "error_rate_threshold_ratio": "0.25",
            "alert_cooldown_seconds": "60",
            "query_limit": "250",
        },
    })

    assert config.enabled is False
    assert config.audit_dir == " ./var/audit "
    assert config.retention_days == 14
    assert config.consecutive_failure_threshold == 4
    assert config.token_daily_threshold == 9000
    assert config.response_timeout_seconds == 3.5
    assert config.permission_denial_window_minutes == 8
    assert config.permission_denial_threshold == 6
    assert config.error_rate_window_minutes == 12
    assert config.error_rate_threshold_ratio == 0.25
    assert config.alert_cooldown_seconds == 60
    assert config.query_limit == 250


def test_load_audit_config_uses_defaults_for_non_mapping_section() -> None:
    assert load_audit_config({"audit": "enabled"}) == AuditConfig()
    assert load_audit_config({}) == AuditConfig()


def test_audit_config_clamps_unsafe_values() -> None:
    config = AuditConfig(
        retention_days=-3,
        consecutive_failure_threshold=0,
        token_daily_threshold=-1,
        response_timeout_seconds=-2,
        permission_denial_window_minutes=0,
        permission_denial_threshold=-1,
        error_rate_window_minutes=0,
        error_rate_threshold_ratio=4.0,
        alert_cooldown_seconds=-5,
        query_limit=100_000,
    )

    assert config.retention_days == 0
    assert config.consecutive_failure_threshold == 1
    assert config.token_daily_threshold == 0
    assert config.response_timeout_seconds == 0
    assert config.permission_denial_window_minutes == 1
    assert config.permission_denial_threshold == 1
    assert config.error_rate_window_minutes == 1
    assert config.error_rate_threshold_ratio == 1
    assert config.alert_cooldown_seconds == 0
    assert config.query_limit == 10_000


def test_audit_config_clamps_ratio_and_query_limit_lower_bounds() -> None:
    config = AuditConfig(error_rate_threshold_ratio=-1, query_limit=0)

    assert config.error_rate_threshold_ratio == 0
    assert config.query_limit == 1


def test_resolve_audit_dir_uses_configured_path(tmp_path) -> None:
    target = tmp_path / "nested" / "audit"
    config = AuditConfig(audit_dir=f"  {target}  ")

    assert config.resolve_audit_dir() == target.resolve()
