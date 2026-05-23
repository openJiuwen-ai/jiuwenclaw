# coding: utf-8
from __future__ import annotations

from jiuwenswarm.agents.harness.common.security_review.schema import (
    FailureClass,
    SecurityEvent,
    SecurityReviewConfig,
    SecuritySignal,
    Severity,
)
from jiuwenswarm.agents.harness.common.security_review.session_state import (
    SecuritySessionState,
)


def test_ring_buffer_keeps_latest_events():
    state = SecuritySessionState(SecurityReviewConfig(ring_buffer_size=2))

    state.record_event(SecurityEvent("tool_call", "s1", iteration=1))
    state.record_event(SecurityEvent("tool_call", "s1", iteration=2))
    state.record_event(SecurityEvent("tool_call", "s1", iteration=3))

    assert [event.iteration for event in state.snapshot_events("s1")] == [2, 3]


def test_high_risk_signal_creates_consumable_advice():
    state = SecuritySessionState(SecurityReviewConfig())
    signal = SecuritySignal(
        signal_type="dangerous_command",
        severity=Severity.HIGH,
        session_id="s1",
        tool_name="bash",
        evidence="curl | sh",
    )

    state.record_signals([signal])

    advice = state.consume_advice("s1")
    assert advice is not None
    assert "安全监督提示" in advice.content
    assert state.consume_advice("s1") is None


def test_repeated_tool_failure_creates_advice_and_request():
    state = SecuritySessionState(SecurityReviewConfig(repeated_tool_failure_threshold=2))
    signal = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.MEDIUM,
        session_id="s1",
        iteration=1,
        tool_name="read_file",
        failure_class=FailureClass.CROSS_WORKSPACE_DENIED,
        evidence="/Users/alice/private.txt",
    )

    first = state.record_signals([signal])
    second = state.record_signals([signal])

    assert first == []
    assert len(second) == 1
    assert second[0].signal_type == "repeated_tool_failure"
    assert second[0].failure_class == FailureClass.CROSS_WORKSPACE_DENIED
    assert "read_file" in state.consume_advice("s1").content


def test_repeated_failures_are_counted_by_tool_and_failure_class():
    state = SecuritySessionState(SecurityReviewConfig(repeated_tool_failure_threshold=2))
    permission = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.MEDIUM,
        session_id="s1",
        tool_name="read_file",
        failure_class=FailureClass.PERMISSION_DENIED,
    )
    sandbox = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.MEDIUM,
        session_id="s1",
        tool_name="read_file",
        failure_class=FailureClass.SANDBOX_DENIED,
    )

    state.record_signals([permission])
    result = state.record_signals([sandbox])

    assert result == []


def test_post_threshold_repeated_failure_recreates_advice_after_consumed():
    state = SecuritySessionState(SecurityReviewConfig(repeated_tool_failure_threshold=2))
    signal = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.MEDIUM,
        session_id="s1",
        tool_name="read_file",
        failure_class=FailureClass.PERMISSION_DENIED,
    )

    state.record_signals([signal])
    threshold = state.record_signals([signal])
    assert len(threshold) == 1
    assert state.consume_advice("s1") is not None

    repeated = state.record_signals([signal])

    assert len(repeated) == 1
    assert repeated[0].signal_type == "repeated_tool_failure"
    assert state.consume_advice("s1") is not None


def test_repeated_failure_counts_are_isolated_by_session():
    state = SecuritySessionState(SecurityReviewConfig(repeated_tool_failure_threshold=2))
    s1_signal = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.MEDIUM,
        session_id="s1",
        tool_name="read_file",
        failure_class=FailureClass.PERMISSION_DENIED,
    )
    s2_signal = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.MEDIUM,
        session_id="s2",
        tool_name="read_file",
        failure_class=FailureClass.PERMISSION_DENIED,
    )

    state.record_signals([s1_signal])
    result = state.record_signals([s2_signal])

    assert result == []
    assert state.counter_snapshot("s1") == {
        "read_file:permission_boundary_hit:permission_denied:": 1
    }
    assert state.counter_snapshot("s2") == {
        "read_file:permission_boundary_hit:permission_denied:": 1
    }


def test_repeated_failures_are_counted_by_reason_code():
    state = SecuritySessionState(SecurityReviewConfig(repeated_tool_failure_threshold=2))
    generic = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.MEDIUM,
        session_id="s1",
        tool_name="read_file",
        failure_class=FailureClass.PERMISSION_DENIED,
        reason_code="generic_permission_denied",
    )
    policy = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.HIGH,
        session_id="s1",
        tool_name="read_file",
        failure_class=FailureClass.BLOCKED_BY_POLICY,
        reason_code="permission_denied_policy",
    )

    state.record_signals([generic])
    result = state.record_signals([policy])

    assert result == []
    assert state.counter_snapshot("s1") == {
        "read_file:permission_boundary_hit:permission_denied:generic_permission_denied": 1,
        "read_file:permission_boundary_hit:blocked_by_policy:permission_denied_policy": 1,
    }


def test_repeated_generic_permission_denial_derives_policy_gap():
    state = SecuritySessionState(SecurityReviewConfig(repeated_tool_failure_threshold=2))
    signal = SecuritySignal(
        signal_type="permission_boundary_hit",
        severity=Severity.MEDIUM,
        session_id="s1",
        iteration=3,
        tool_name="read_file",
        failure_class=FailureClass.PERMISSION_DENIED,
        evidence="Permission denied",
        source="tool_result",
        confidence="regex_low",
        reason_code="generic_permission_denied",
    )

    state.record_signals([signal])
    generated = state.record_signals([signal])

    assert [item.signal_type for item in generated] == [
        "repeated_tool_failure",
        "policy_rule_gap",
    ]
    assert generated[0].reason_code == "repeated_tool_failure"
    assert generated[1].reason_code == "policy_gap_repeated_generic_permission"
    assert all(item.source == "derived" for item in generated)


def test_repeated_approval_required_derives_approval_boundary_gap():
    state = SecuritySessionState(SecurityReviewConfig(repeated_tool_failure_threshold=2))
    signal = SecuritySignal(
        signal_type="approval_required",
        severity=Severity.MEDIUM,
        session_id="s1",
        tool_name="bash",
        failure_class=FailureClass.PERMISSION_DENIED,
        reason_code="approval_required",
    )

    state.record_signals([signal])
    generated = state.record_signals([signal])

    assert [item.signal_type for item in generated] == [
        "repeated_tool_failure",
        "approval_boundary_gap",
    ]
    assert generated[1].reason_code == "approval_boundary_gap"


def test_user_rejection_does_not_derive_repeated_failure():
    state = SecuritySessionState(SecurityReviewConfig(repeated_tool_failure_threshold=2))
    signal = SecuritySignal(
        signal_type="user_rejected_permission",
        severity=Severity.LOW,
        session_id="s1",
        tool_name="bash",
        failure_class=FailureClass.PERMISSION_DENIED,
        reason_code="user_rejected_permission",
    )

    state.record_signals([signal])
    generated = state.record_signals([signal])

    assert generated == []


def test_max_session_eviction_removes_old_counters_events_and_advice():
    state = SecuritySessionState(SecurityReviewConfig(max_sessions=1))
    old_signal = SecuritySignal(
        signal_type="dangerous_command",
        severity=Severity.HIGH,
        session_id="old",
        tool_name="bash",
        failure_class=FailureClass.PERMISSION_DENIED,
    )

    state.record_event(SecurityEvent("tool_call", "old", iteration=1))
    state.record_signals([old_signal])
    state.record_event(SecurityEvent("tool_call", "new", iteration=2))

    assert state.snapshot_events("old") == []
    assert state.counter_snapshot("old") == {}
    assert state.consume_advice("old") is None
    assert [event.iteration for event in state.snapshot_events("new")] == [2]
