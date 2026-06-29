from __future__ import annotations

import pytest

from jiuwenswarm.gateway.cron.models import (
    CRON_DEFAULT_TIMEOUT_SECONDS,
    CRON_JOB_DEFAULT_MODE,
    CRON_JOB_MODES,
    CRON_MAX_TIMEOUT_SECONDS,
    CRON_TEAM_DEFAULT_TIMEOUT_SECONDS,
    CronJob,
    coerce_cron_job_mode,
    cron_job_metadata,
    is_team_cron_mode,
    normalize_cron_job_mode,
    normalize_cron_job_timeout_seconds,
    resolve_cron_job_timeout_seconds,
)


@pytest.mark.parametrize("mode", sorted(CRON_JOB_MODES))
def test_normalize_cron_job_mode_accepts_supported_values(mode: str) -> None:
    assert normalize_cron_job_mode(mode) == mode
    assert normalize_cron_job_mode(mode.upper()) == mode


def test_normalize_cron_job_mode_defaults_to_agent_fast() -> None:
    assert normalize_cron_job_mode(None) == CRON_JOB_DEFAULT_MODE
    assert normalize_cron_job_mode("") == CRON_JOB_DEFAULT_MODE
    assert CRON_JOB_DEFAULT_MODE == "agent.fast"


def test_normalize_cron_job_mode_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Invalid cron job mode"):
        normalize_cron_job_mode("unknown-mode")


@pytest.mark.parametrize(
    "mode",
    ["team", "team.plan", "code.team", "TEAM"],
)
def test_is_team_cron_mode_true(mode: str) -> None:
    assert is_team_cron_mode(mode) is True


@pytest.mark.parametrize(
    "mode",
    ["agent", "plan", "agent.plan", "", None],
)
def test_is_team_cron_mode_false(mode: str | None) -> None:
    assert is_team_cron_mode(mode) is False


def test_coerce_cron_job_mode_passthrough_unknown() -> None:
    assert coerce_cron_job_mode("future.mode") == "future.mode"
    assert coerce_cron_job_mode("Future.Mode") == "future.mode"


def test_coerce_cron_job_mode_known_values() -> None:
    assert coerce_cron_job_mode("team") == "team"
    assert coerce_cron_job_mode(None, default=CRON_JOB_DEFAULT_MODE) == CRON_JOB_DEFAULT_MODE


def test_cron_job_default_mode_matches_normalize_default() -> None:
    assert normalize_cron_job_mode(None) == CRON_JOB_DEFAULT_MODE


def test_cron_job_metadata_matches_modes_and_default() -> None:
    meta = cron_job_metadata()
    assert set(meta["modes"]) == CRON_JOB_MODES
    assert meta["default_mode"] == CRON_JOB_DEFAULT_MODE
    assert meta["default_timeout_seconds"] == CRON_DEFAULT_TIMEOUT_SECONDS
    assert meta["default_team_timeout_seconds"] == CRON_TEAM_DEFAULT_TIMEOUT_SECONDS
    assert meta["max_timeout_seconds"] == CRON_MAX_TIMEOUT_SECONDS
    assert meta["modes"] == sorted(meta["modes"])


def test_resolve_cron_job_timeout_seconds_defaults_by_mode() -> None:
    normal_job = CronJob(
        id="j1",
        name="normal",
        enabled=True,
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        description="task",
        targets="tui",
        mode="agent.fast",
    )
    team_job = CronJob(
        id="j2",
        name="team",
        enabled=True,
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        description="task",
        targets="tui",
        mode="team",
    )
    assert resolve_cron_job_timeout_seconds(normal_job) == CRON_DEFAULT_TIMEOUT_SECONDS
    assert resolve_cron_job_timeout_seconds(team_job) == CRON_TEAM_DEFAULT_TIMEOUT_SECONDS


def test_resolve_cron_job_timeout_seconds_uses_user_override() -> None:
    job = CronJob(
        id="j3",
        name="custom",
        enabled=True,
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        description="task",
        targets="tui",
        mode="team",
        timeout_seconds=1800,
    )
    assert resolve_cron_job_timeout_seconds(job) == 1800


def test_normalize_cron_job_timeout_seconds_rejects_invalid_values() -> None:
    assert normalize_cron_job_timeout_seconds(None) is None
    with pytest.raises(ValueError, match="at least 60"):
        normalize_cron_job_timeout_seconds(30)
    assert normalize_cron_job_timeout_seconds(CRON_MAX_TIMEOUT_SECONDS) == CRON_MAX_TIMEOUT_SECONDS
    with pytest.raises(ValueError, match="at most"):
        normalize_cron_job_timeout_seconds(CRON_MAX_TIMEOUT_SECONDS + 1)
