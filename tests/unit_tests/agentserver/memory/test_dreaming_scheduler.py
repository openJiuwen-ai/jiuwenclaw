# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for Dreaming cron scheduling."""
from __future__ import annotations

import sys
import types
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from jiuwenswarm.agents.harness.common.memory import dreaming
from jiuwenswarm.agents.harness.common.memory.dreaming.scheduler import (
    CronDreamingOrchestrator,
    _delay_until,
    _next_scheduled_time,
)
from jiuwenswarm.agents.harness.common.memory.dreaming.sweeper import (
    DreamingConfig,
)


def _load_config(
    monkeypatch: pytest.MonkeyPatch,
    config: dict,
    *,
    mode: str = "agent",
) -> DreamingConfig:
    config_module = types.ModuleType("jiuwenswarm.common.config")
    config_module.get_config = lambda: config
    monkeypatch.setitem(
        sys.modules,
        "jiuwenswarm.common.config",
        config_module,
    )
    for env_name in (
        "DREAMING_AGENT_ENABLED",
        "DREAMING_CODE_ENABLED",
        "DREAMING_INTERVAL",
        "DREAMING_CRON_EXPR",
        "DREAMING_TIMEZONE",
    ):
        monkeypatch.delenv(env_name, raising=False)
    return DreamingConfig.load(mode)


def test_next_scheduled_time_at_midnight() -> None:
    timezone = ZoneInfo("Asia/Shanghai")
    base_time = datetime(2026, 7, 26, 23, 30, tzinfo=timezone)

    next_time = _next_scheduled_time("0 0 * * *", base_time)

    assert next_time == datetime(
        2026,
        7,
        27,
        0,
        0,
        tzinfo=timezone,
    )


def test_next_scheduled_time_accepts_seven_field_quartz() -> None:
    timezone = ZoneInfo("Asia/Shanghai")
    base_time = datetime(2026, 7, 26, 23, 30, tzinfo=timezone)

    next_time = _next_scheduled_time(
        "0 0 0 * * ? *",
        base_time,
    )

    assert next_time == datetime(
        2026,
        7,
        27,
        0,
        0,
        tzinfo=timezone,
    )


def test_delay_until_accounts_for_daylight_saving_change() -> None:
    timezone = ZoneInfo("Europe/Berlin")
    now = datetime(2026, 10, 25, 1, 30, tzinfo=timezone)
    next_time = datetime(2026, 10, 25, 3, 30, tzinfo=timezone)

    assert _delay_until(next_time, now) == 10800.0


def test_cron_orchestrator_rejects_invalid_expression() -> None:
    async def sweep() -> None:
        return None

    with pytest.raises(ValueError, match="5 or 7 fields"):
        CronDreamingOrchestrator(
            sweep_fn=sweep,
            cron_expr="not a cron expression",
            timezone="Asia/Shanghai",
        )


def test_config_loads_cron_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _load_config(
        monkeypatch,
        {
            "memory": {
                "dreaming": {
                    "agent": {
                        "enabled": True,
                        "cron_expr": "0 0 * * *",
                        "timezone": "Asia/Shanghai",
                    }
                }
            }
        },
    )

    assert cfg.enabled is True
    assert cfg.cron_expr == "0 0 * * *"
    assert cfg.timezone == "Asia/Shanghai"


def test_cron_environment_overrides_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _load_config(
        monkeypatch,
        {
            "memory": {
                "dreaming": {
                    "agent": {
                        "enabled": True,
                        "cron_expr": "0 1 * * *",
                        "timezone": "UTC",
                    }
                }
            }
        },
    )
    monkeypatch.setenv("DREAMING_CRON_EXPR", "0 2 * * *")
    monkeypatch.setenv("DREAMING_TIMEZONE", "Asia/Shanghai")

    cfg = DreamingConfig.load("agent")

    assert cfg.cron_expr == "0 2 * * *"
    assert cfg.timezone == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_start_dreaming_selects_cron_orchestrator(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.agents.harness.common.memory.dreaming import (
        sweeper as sweeper_module,
    )

    cfg = DreamingConfig(
        enabled=True,
        cron_expr="0 0 * * *",
        timezone="Asia/Shanghai",
    )
    monkeypatch.setattr(
        sweeper_module.DreamingConfig,
        "load",
        classmethod(lambda cls, mode: cfg),
    )
    monkeypatch.setattr(sweeper_module.Sweeper, "init", lambda self: None)

    captured: dict = {}

    class FakeCronOrchestrator:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        async def start(self) -> None:
            captured["started"] = True

        async def stop(self) -> None:
            captured["stopped"] = True

    monkeypatch.setattr(
        dreaming,
        "CronDreamingOrchestrator",
        FakeCronOrchestrator,
    )
    dreaming._orchestrators.clear()

    try:
        orchestrator = await dreaming.start_dreaming(
            sessions_dir=str(tmp_path / "sessions"),
            output_dir=str(tmp_path / "memory"),
            mode="agent",
        )

        assert isinstance(orchestrator, FakeCronOrchestrator)
        assert captured["cron_expr"] == "0 0 * * *"
        assert captured["timezone"] == "Asia/Shanghai"
        assert captured["started"] is True
    finally:
        await dreaming.stop_dreaming("agent")
