# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persistent cumulative usage statistics (survives mission deletion)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from jiuwenavatar.gateway.report.models import Mission, MissionStatus
from jiuwenavatar.gateway.report.stats import _local_date, _mission_duration_seconds

logger = logging.getLogger(__name__)

_TERMINAL = frozenset(
    {
        MissionStatus.COMPLETED,
        MissionStatus.FAILED,
        MissionStatus.CANCELLED,
    }
)


class MissionSnapshot(BaseModel):
    """Per-mission contribution kept after mission list deletion."""

    day: str
    duration_seconds: int = 0
    completed_counted: bool = False
    terminal: bool = False


class UsageStatsLedger(BaseModel):
    version: int = 1
    active_days: list[str] = Field(default_factory=list)
    total_duration_seconds: int = 0
    completed_tasks: int = 0
    total_dispatched: int = 0
    first_task_date: str | None = None
    last_task_date: str | None = None
    missions: dict[str, MissionSnapshot] = Field(default_factory=dict)


def _get_usage_stats_path() -> Path:
    from jiuwenavatar.common.utils import get_user_workspace_dir

    return get_user_workspace_dir() / "reports" / "usage_stats.json"


def load_usage_ledger(path: Path | None = None) -> UsageStatsLedger:
    stats_path = path or _get_usage_stats_path()
    if not stats_path.is_file():
        return UsageStatsLedger()
    try:
        raw = json.loads(stats_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return UsageStatsLedger.model_validate(raw)
    except Exception as exc:
        logger.warning("Failed to load usage stats %s: %s", stats_path, exc)
    return UsageStatsLedger()


def save_usage_ledger(ledger: UsageStatsLedger, path: Path | None = None) -> None:
    stats_path = path or _get_usage_stats_path()
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(
        json.dumps(ledger.model_dump(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _add_active_day(ledger: UsageStatsLedger, day: str) -> None:
    if day not in ledger.active_days:
        ledger.active_days.append(day)
        ledger.active_days.sort()
    if ledger.first_task_date is None or day < ledger.first_task_date:
        ledger.first_task_date = day
    if ledger.last_task_date is None or day > ledger.last_task_date:
        ledger.last_task_date = day


def sync_mission_to_ledger(
    ledger: UsageStatsLedger,
    mission: Mission,
    *,
    now: datetime | None = None,
) -> None:
    """Idempotently record dispatch / duration / completion for one mission."""
    if not mission.started_at:
        return

    now = now or datetime.now()
    day = _local_date(mission.started_at).isoformat()
    snap = ledger.missions.get(mission.id)

    if snap is None:
        _add_active_day(ledger, day)
        ledger.total_dispatched += 1
        snap = MissionSnapshot(day=day)
        ledger.missions[mission.id] = snap

    new_duration = int(_mission_duration_seconds(mission, now=now))
    delta = new_duration - snap.duration_seconds
    if delta > 0:
        ledger.total_duration_seconds += delta
        snap.duration_seconds = new_duration

    is_terminal = mission.status in _TERMINAL
    if mission.status == MissionStatus.COMPLETED and not snap.completed_counted:
        ledger.completed_tasks += 1
        snap.completed_counted = True
    if is_terminal:
        snap.terminal = True


def backfill_ledger_from_missions(
    ledger: UsageStatsLedger,
    missions: list[Mission],
    *,
    now: datetime | None = None,
) -> UsageStatsLedger:
    for mission in missions:
        sync_mission_to_ledger(ledger, mission, now=now)
    return ledger


def build_usage_stats_response(
    ledger: UsageStatsLedger,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    today = today or date.today()
    today_str = today.isoformat()
    today_tasks = sum(1 for snap in ledger.missions.values() if snap.day == today_str)
    active_days = sorted(set(ledger.active_days))
    return {
        "active_days": len(active_days),
        "total_duration_seconds": ledger.total_duration_seconds,
        "used_today": today_str in active_days,
        "today_tasks": today_tasks,
        "completed_tasks": ledger.completed_tasks,
        "total_tasks": ledger.total_dispatched,
        "first_task_date": ledger.first_task_date,
        "last_task_date": ledger.last_task_date,
    }


def record_mission(mission: Mission, *, now: datetime | None = None) -> None:
    """Append/update cumulative stats; safe to call on create and status changes."""
    from jiuwenavatar.common.enterprise import is_enterprise_mode

    # Enterprise stats are computed from tenant-scoped mission lists; do not
    # merge into the standalone cumulative ledger under ~/.jiuwenavatar.
    if is_enterprise_mode():
        return

    ledger = load_usage_ledger()
    sync_mission_to_ledger(ledger, mission, now=now)
    save_usage_ledger(ledger)


def get_usage_stats(
    missions: list[Mission] | None = None,
    *,
    today: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Load cumulative stats; optionally backfill/sync from current mission list."""
    now = now or datetime.now()
    ledger = load_usage_ledger()
    if missions:
        backfill_ledger_from_missions(ledger, missions, now=now)
        save_usage_ledger(ledger)
    return build_usage_stats_response(ledger, today=today)
