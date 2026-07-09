# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Report Manager — 任务与报告管理器."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from jiuwenavatar.gateway.report.models import Mission, MissionReport, MissionStatus
from jiuwenavatar.gateway.report.store import ReportStore

logger = logging.getLogger(__name__)


class ReportManager:
    """Singleton manager for missions and reports."""

    _instance: ReportManager | None = None

    def __init__(self) -> None:
        self._store = ReportStore()

    @classmethod
    def get_instance(cls) -> ReportManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    # ------------------------------------------------------------------
    # Mission lifecycle
    # ------------------------------------------------------------------

    def create_mission(self, *, avatar_id: str, trigger_id: str | None, prompt: str) -> Mission:
        mission = Mission(avatar_id=avatar_id, trigger_id=trigger_id, prompt=prompt)
        self._store.save_mission(mission)
        self._record_usage(mission)
        logger.info("Created mission %s for avatar %s", mission.id, avatar_id)
        return mission

    def update_mission_status(self, mission_id: str, status: MissionStatus, *, result_summary: str | None = None) -> Mission | None:
        mission = self._store.get_mission(mission_id)
        if mission is None:
            return None
        if mission.status == MissionStatus.CANCELLED and status in (MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.RUNNING):
            return mission
        mission.status = status
        if result_summary is not None:
            mission.result_summary = result_summary
        if status in (MissionStatus.COMPLETED, MissionStatus.FAILED, MissionStatus.CANCELLED):
            mission.completed_at = datetime.now().isoformat()
        self._store.save_mission(mission)
        self._record_usage(mission)
        return mission

    def update_mission_runtime(self, mission_id: str, *, run_id: str | None = None, session_id: str | None = None) -> Mission | None:
        mission = self._store.get_mission(mission_id)
        if mission is None:
            return None
        if run_id is not None:
            mission.run_id = run_id
        if session_id is not None:
            mission.session_id = session_id
        self._store.save_mission(mission)
        return mission

    def cancel_mission(self, mission_id: str, *, result_summary: str = "用户已取消任务") -> Mission | None:
        mission = self._store.get_mission(mission_id)
        if mission is None:
            return None
        if mission.status not in (MissionStatus.PENDING, MissionStatus.RUNNING):
            return mission
        now = datetime.now().isoformat()
        mission.status = MissionStatus.CANCELLED
        mission.completed_at = now
        mission.cancel_requested_at = now
        mission.result_summary = result_summary
        self._store.save_mission(mission)
        self._record_usage(mission)
        return mission

    def _record_usage(self, mission: Mission) -> None:
        from jiuwenavatar.gateway.report.usage_stats import record_mission

        try:
            record_mission(mission)
        except Exception:
            logger.warning("Failed to record usage stats for mission %s", mission.id, exc_info=True)

    # ------------------------------------------------------------------
    # Report CRUD
    # ------------------------------------------------------------------

    def create_report(
        self,
        *,
        mission_id: str,
        avatar_id: str,
        avatar_persona: str = "",
        title: str = "执行报告",
        summary: str = "",
        sections: list[dict[str, Any]] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> MissionReport:
        from jiuwenavatar.gateway.report.models import ReportSection

        report = MissionReport(
            mission_id=mission_id,
            avatar_id=avatar_id,
            avatar_persona=avatar_persona,
            title=title,
            summary=summary,
            sections=[ReportSection(**s) for s in (sections or [])],
            metrics=metrics or {},
        )
        self._store.save_report(report)
        logger.info("Created report %s for mission %s", report.id, mission_id)
        return report

    # ------------------------------------------------------------------
    # List / Get
    # ------------------------------------------------------------------

    def list_missions(self, *, avatar_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return [m.model_dump() for m in self._store.list_missions(avatar_id=avatar_id, limit=limit)]

    def get_mission(self, mission_id: str) -> dict[str, Any] | None:
        m = self._store.get_mission(mission_id)
        return m.model_dump() if m else None

    def delete_mission(self, mission_id: str) -> bool:
        # Usage stats in usage_stats.json are cumulative and intentionally kept.
        return self._store.delete_mission(mission_id)

    def purge_avatar_records(self, avatar_id: str) -> dict[str, int]:
        """Remove all missions and reports belonging to an avatar."""
        missions = self._store.delete_missions_for_avatar(avatar_id)
        reports = self._store.delete_reports_for_avatar(avatar_id)
        if missions or reports:
            logger.info(
                "Purged avatar records avatar_id=%s missions=%d reports=%d",
                avatar_id,
                missions,
                reports,
            )
        return {"missions": missions, "reports": reports}

    def list_reports(self, *, avatar_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return [r.model_dump() for r in self._store.list_reports(avatar_id=avatar_id, limit=limit)]

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        r = self._store.get_report(report_id)
        return r.model_dump() if r else None

    # ------------------------------------------------------------------
    # WebSocket API Handlers
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_limit(value: Any, default: int = 100, maximum: int = 500) -> int:
        try:
            limit = int(value)
        except (TypeError, ValueError):
            limit = default
        return max(1, min(limit, maximum))

    async def handle_missions_list(self, **kwargs: Any) -> dict[str, Any]:
        avatar_id = kwargs.get("avatar_id") or None
        limit = self._coerce_limit(kwargs.get("limit"))
        return {"missions": self.list_missions(avatar_id=avatar_id, limit=limit)}

    async def handle_missions_get(self, *, mission_id: str, **kwargs: Any) -> dict[str, Any]:
        mission = self.get_mission(mission_id)
        if mission is None:
            return {"error": f"Mission not found: {mission_id}"}
        return {"mission": mission}

    async def handle_missions_cancel(self, *, mission_id: str, **kwargs: Any) -> dict[str, Any]:
        mission = self.cancel_mission(mission_id)
        if mission is None:
            return {"error": f"Mission not found: {mission_id}"}
        return {"mission": mission.model_dump()}

    async def handle_missions_delete(self, *, mission_id: str, **kwargs: Any) -> dict[str, Any]:
        deleted = self.delete_mission(mission_id)
        if not deleted:
            return {"error": f"Mission not found: {mission_id}"}
        return {"success": True, "mission_id": mission_id}

    async def handle_reports_list(self, **kwargs: Any) -> dict[str, Any]:
        avatar_id = kwargs.get("avatar_id") or None
        limit = self._coerce_limit(kwargs.get("limit"))
        return {"reports": self.list_reports(avatar_id=avatar_id, limit=limit)}

    async def handle_reports_get(self, *, report_id: str, **kwargs: Any) -> dict[str, Any]:
        report = self.get_report(report_id)
        if report is None:
            return {"error": f"Report not found: {report_id}"}
        return {"report": report}

    async def handle_report_read_state_get(self, **kwargs: Any) -> dict[str, Any]:
        from jiuwenavatar.gateway.report.read_state import load_read_state

        return {"read_state": load_read_state()}

    async def handle_report_read_state_set(self, **kwargs: Any) -> dict[str, Any]:
        from jiuwenavatar.gateway.report.read_state import load_read_state, merge_read_state

        patch = kwargs.get("read_state")
        if patch is None or not isinstance(patch, dict):
            return {"read_state": load_read_state(), "error": "read_state parameter required"}
        missions = patch.get("missions")
        reports = patch.get("reports")
        payload: dict[str, Any] = {}
        if isinstance(missions, dict):
            payload["missions"] = missions
        if isinstance(reports, dict):
            payload["reports"] = reports
        return {"read_state": merge_read_state(payload)}

    async def handle_report_unread_counts(self, **kwargs: Any) -> dict[str, Any]:
        from jiuwenavatar.gateway.report.read_state import (
            count_active_missions_by_avatar,
            count_unread_missions_by_avatar,
        )

        limit = self._coerce_limit(kwargs.get("limit"), default=500, maximum=1000)
        return {
            "missions_by_avatar": count_unread_missions_by_avatar(limit=limit),
            "active_by_avatar": count_active_missions_by_avatar(limit=limit),
        }

    async def handle_missions_stats(self, **kwargs: Any) -> dict[str, Any]:
        from jiuwenavatar.gateway.report.usage_stats import get_usage_stats

        avatar_id = kwargs.get("avatar_id") or None
        limit = self._coerce_limit(kwargs.get("limit"), default=10000, maximum=100000)
        missions = self._store.list_missions(avatar_id=avatar_id, limit=limit)
        return {"stats": get_usage_stats(missions)}


def get_report_manager() -> ReportManager:
    return ReportManager.get_instance()
