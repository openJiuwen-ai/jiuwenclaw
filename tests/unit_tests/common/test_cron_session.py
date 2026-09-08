# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for jiuwenswarm.common.cron_session."""

from __future__ import annotations

import pytest

from jiuwenswarm.common.cron_session import is_cron_execution_session


@pytest.mark.parametrize(
    "session_id",
    [
        "cron_19abc_job1",
        "cron_jobid",
        " cron_leading_space",
        "cron",
    ],
)
def test_is_cron_execution_session_true(session_id: str) -> None:
    """cron-prefixed session ids are recognised as cron execution sessions."""
    assert is_cron_execution_session(session_id) is True


@pytest.mark.parametrize(
    "session_id",
    [
        "sess_19abc",
        "heartbeat_1",
        "agent_chat_xyz",
        "CronCapitalised",  # case-sensitive, capital C does NOT trigger
        "",
    ],
)
def test_is_cron_execution_session_false(session_id: str) -> None:
    """non-cron session ids must not be flagged as cron execution sessions."""
    assert is_cron_execution_session(session_id) is False


def test_is_cron_execution_session_none_safe() -> None:
    """None and non-string inputs must short-circuit to False."""
    assert is_cron_execution_session(None) is False
    assert is_cron_execution_session(123) is False  # type: ignore[arg-type]
    assert is_cron_execution_session(["cron_x"]) is False  # type: ignore[arg-type]
