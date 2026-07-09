# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Mission usage statistics."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from jiuwenavatar.gateway.report.models import Mission, MissionStatus


def _parse_iso(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _local_date(iso: str) -> date:
    dt = _parse_iso(iso)
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.date()


def _mission_duration_seconds(mission: Mission, *, now: datetime | None = None) -> float:
    start = _parse_iso(mission.started_at)
    if mission.completed_at:
        end = _parse_iso(mission.completed_at)
    else:
        end = now or datetime.now()
    if start.tzinfo is not None:
        start = start.astimezone().replace(tzinfo=None)
    if end.tzinfo is not None:
        end = end.astimezone().replace(tzinfo=None)
    return max(0.0, (end - start).total_seconds())


def compute_mission_usage_stats(
    missions: list[Mission],
    *,
    today: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate usage stats from mission records.

    - active_days: distinct calendar days with at least one dispatched task (started_at)
    - total_duration_seconds: sum of task run durations (running tasks use *now* as end)
    - used_today: any task started today
    - completed_tasks: missions with status completed
    """
    today = today or date.today()
    now = now or datetime.now()

    active_day_set: set[date] = set()
    total_duration_seconds = 0.0
    completed_tasks = 0
    used_today = False
    today_tasks = 0

    for mission in missions:
        if not mission.started_at:
            continue
        day = _local_date(mission.started_at)
        active_day_set.add(day)
        if day == today:
            used_today = True
            today_tasks += 1

        total_duration_seconds += _mission_duration_seconds(mission, now=now)

        if mission.status == MissionStatus.COMPLETED:
            completed_tasks += 1

    sorted_days = sorted(active_day_set)
    return {
        "active_days": len(active_day_set),
        "total_duration_seconds": int(total_duration_seconds),
        "used_today": used_today,
        "today_tasks": today_tasks,
        "completed_tasks": completed_tasks,
        "total_tasks": len(missions),
        "first_task_date": sorted_days[0].isoformat() if sorted_days else None,
        "last_task_date": sorted_days[-1].isoformat() if sorted_days else None,
    }
