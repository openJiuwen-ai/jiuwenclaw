# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from datetime import date, datetime
from pathlib import Path

from jiuwenavatar.gateway.report.models import Mission, MissionStatus
from jiuwenavatar.gateway.report.stats import compute_mission_usage_stats
from jiuwenavatar.gateway.report.usage_stats import get_usage_stats


def _mission(
    *,
    mission_id: str,
    started_at: str,
    completed_at: str | None = None,
    status: MissionStatus = MissionStatus.COMPLETED,
) -> Mission:
    return Mission(
        id=mission_id,
        avatar_id="avatar-1",
        trigger_id=None,
        prompt="test",
        started_at=started_at,
        completed_at=completed_at,
        status=status,
    )


def test_compute_mission_usage_stats_basic():
    missions = [
        _mission(
            mission_id="m1",
            started_at="2026-06-10T09:00:00",
            completed_at="2026-06-10T09:30:00",
        ),
        _mission(
            mission_id="m2",
            started_at="2026-06-10T14:00:00",
            completed_at="2026-06-10T15:00:00",
        ),
        _mission(
            mission_id="m3",
            started_at="2026-06-11T10:00:00",
            completed_at="2026-06-11T10:15:00",
            status=MissionStatus.FAILED,
        ),
    ]
    stats = compute_mission_usage_stats(
        missions,
        today=date(2026, 6, 10),
        now=datetime(2026, 6, 10, 16, 0, 0),
    )
    assert stats["active_days"] == 2
    assert stats["total_duration_seconds"] == 6300
    assert stats["used_today"] is True
    assert stats["today_tasks"] == 2
    assert stats["completed_tasks"] == 2
    assert stats["total_tasks"] == 3


def test_compute_mission_usage_stats_not_used_today():
    missions = [
        _mission(
            mission_id="m1",
            started_at="2026-06-09T09:00:00",
            completed_at="2026-06-09T09:10:00",
        ),
    ]
    stats = compute_mission_usage_stats(missions, today=date(2026, 6, 10))
    assert stats["used_today"] is False
    assert stats["active_days"] == 1


def test_usage_stats_survive_mission_deletion(tmp_path: Path, monkeypatch):
    stats_path = tmp_path / "usage_stats.json"
    monkeypatch.setattr(
        "jiuwenavatar.gateway.report.usage_stats._get_usage_stats_path",
        lambda: stats_path,
    )
    mission = _mission(
        mission_id="m-del",
        started_at="2026-06-16T09:00:00",
        completed_at="2026-06-16T09:20:00",
    )
    get_usage_stats([mission], today=date(2026, 6, 16))

    # missions.json 清空后，统计仍保留。
    stats_after_delete = get_usage_stats([], today=date(2026, 6, 16))
    assert stats_after_delete["active_days"] == 1
    assert stats_after_delete["completed_tasks"] == 1
    assert stats_after_delete["total_duration_seconds"] == 1200
    assert stats_after_delete["used_today"] is True


def test_backfill_from_existing_missions(tmp_path: Path, monkeypatch):
    stats_path = tmp_path / "usage_stats.json"
    monkeypatch.setattr(
        "jiuwenavatar.gateway.report.usage_stats._get_usage_stats_path",
        lambda: stats_path,
    )
    missions = [
        _mission(
            mission_id="legacy-1",
            started_at="2026-06-01T10:00:00",
            completed_at="2026-06-01T10:05:00",
        ),
    ]
    stats = get_usage_stats(missions, today=date(2026, 6, 16))
    assert stats["active_days"] == 1
    assert stats["completed_tasks"] == 1
    assert stats["total_tasks"] == 1
