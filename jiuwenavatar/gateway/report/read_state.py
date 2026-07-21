# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""报告/任务已读状态 — 与 Web「报告」页、桌面浮标未读角标共用."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from jiuwenavatar.gateway.report.models import Mission, MissionStatus

logger = logging.getLogger(__name__)

_ACTIVE_MISSION_STATUSES = frozenset({MissionStatus.PENDING, MissionStatus.RUNNING})
_merge_lock = threading.Lock()


def _read_state_path() -> Path:
    from jiuwenavatar.gateway.report.store import _get_report_dir

    return _get_report_dir() / "read_state.json"


def load_read_state() -> dict[str, Any]:
    path = _read_state_path()
    if not path.is_file():
        return {"missions": {}, "reports": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"missions": {}, "reports": {}}
        return {
            "missions": data.get("missions") if isinstance(data.get("missions"), dict) else {},
            "reports": data.get("reports") if isinstance(data.get("reports"), dict) else {},
        }
    except Exception as exc:
        logger.warning("load_read_state failed: %s", exc)
        return {"missions": {}, "reports": {}}


def save_read_state(state: dict[str, Any]) -> None:
    path = _read_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "missions": state.get("missions") if isinstance(state.get("missions"), dict) else {},
        "reports": state.get("reports") if isinstance(state.get("reports"), dict) else {},
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def merge_read_state(patch: dict[str, Any]) -> dict[str, Any]:
    with _merge_lock:
        current = load_read_state()
        for key in ("missions", "reports"):
            section = patch.get(key)
            if isinstance(section, dict):
                current[key] = {**current.get(key, {}), **section}
        save_read_state(current)
        return current


def _mission_status_value(mission: Mission) -> str:
    status = mission.status
    return status.value if hasattr(status, "value") else str(status)


def is_mission_active(mission: Mission) -> bool:
    status = mission.status
    if isinstance(status, MissionStatus):
        return status in _ACTIVE_MISSION_STATUSES
    return str(status) in {s.value for s in _ACTIVE_MISSION_STATUSES}


def is_mission_unread(mission: Mission, state: dict[str, Any] | None = None) -> bool:
    """已结束任务的未读（执行中/等待中不计入未读角标）。"""
    if is_mission_active(mission):
        return False
    st = state if state is not None else load_read_state()
    rec = st.get("missions", {}).get(mission.id)
    if not rec:
        return True
    return rec.get("status") != _mission_status_value(mission)


def is_report_unread(report_id: str, state: dict[str, Any] | None = None) -> bool:
    st = state if state is not None else load_read_state()
    return report_id not in st.get("reports", {})


def count_unread_missions_by_avatar(
    *,
    limit: int = 500,
    group_id: str | None = None,
    owner_user_id: str | None = None,
) -> dict[str, int]:
    from jiuwenavatar.gateway.report.store import ReportStore

    state = load_read_state()
    counts: dict[str, int] = {}
    for mission in ReportStore().list_missions(
        limit=limit,
        group_id=group_id,
        owner_user_id=owner_user_id,
    ):
        avatar_id = (mission.avatar_id or "").strip()
        if not avatar_id or not is_mission_unread(mission, state):
            continue
        counts[avatar_id] = counts.get(avatar_id, 0) + 1
    return counts


def count_active_missions_by_avatar(
    *,
    limit: int = 500,
    group_id: str | None = None,
    owner_user_id: str | None = None,
) -> dict[str, int]:
    """按分身统计执行中/等待中的任务数（用于浮标忙碌指示）。"""
    from jiuwenavatar.gateway.report.store import ReportStore

    counts: dict[str, int] = {}
    for mission in ReportStore().list_missions(
        limit=limit,
        group_id=group_id,
        owner_user_id=owner_user_id,
    ):
        avatar_id = (mission.avatar_id or "").strip()
        if not avatar_id or not is_mission_active(mission):
            continue
        counts[avatar_id] = counts.get(avatar_id, 0) + 1
    return counts
