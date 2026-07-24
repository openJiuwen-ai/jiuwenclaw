# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for MemberDropoutTracker."""

from __future__ import annotations

from jiuwenswarm.agents.dropout.member_tracker import MemberDropoutTracker


def test_below_threshold_does_not_drop():
    tracker = MemberDropoutTracker(drop_after_failures=2, min_active_members=2)
    decision = tracker.record_failure("alice", active_members=3)
    assert decision.should_drop is False
    assert decision.failure_count == 1
    assert decision.collapse_fallback is False


def test_drop_after_threshold_when_team_safe():
    tracker = MemberDropoutTracker(drop_after_failures=2, min_active_members=2)
    tracker.record_failure("alice", active_members=3)
    decision = tracker.record_failure("alice", active_members=3)
    assert decision.should_drop is True
    assert tracker.is_dropped("alice") is True
    assert decision.failure_count == 2


def test_collapse_fallback_blocks_drop():
    tracker = MemberDropoutTracker(drop_after_failures=1, min_active_members=2)
    # active_members=2 → remaining would be 1 < min_active_members=2
    decision = tracker.record_failure("alice", active_members=2)
    assert decision.should_drop is False
    assert decision.collapse_fallback is True
    assert tracker.is_dropped("alice") is False


def test_record_pass_clears_failure_streak():
    tracker = MemberDropoutTracker(drop_after_failures=2, min_active_members=1)
    tracker.record_failure("alice", active_members=3)
    tracker.record_pass("alice")
    assert tracker.failure_count("alice") == 0
    decision = tracker.record_failure("alice", active_members=3)
    assert decision.should_drop is False
    assert decision.failure_count == 1
