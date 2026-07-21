# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Report store — 报告持久化存储."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from jiuwenavatar.common.postgres_json_store import (
    PostgresJsonStore,
    PostgresJsonStoreUnavailable,
    postgres_store_enabled,
)
from jiuwenavatar.gateway.report.models import Mission, MissionReport

logger = logging.getLogger(__name__)


def _get_report_dir() -> Path:
    from jiuwenavatar.common.utils import get_user_workspace_dir
    return get_user_workspace_dir() / "reports"


class ReportStore:
    """Persist missions and reports to user workspace."""

    def __init__(self, path: Path | None = None) -> None:
        self._dir = path or _get_report_dir()
        self._missions_pg: PostgresJsonStore | None = None
        self._reports_pg: PostgresJsonStore | None = None
        if path is None and postgres_store_enabled():
            try:
                self._missions_pg = PostgresJsonStore("missions")
                self._reports_pg = PostgresJsonStore("mission_reports")
            except PostgresJsonStoreUnavailable as exc:
                logger.warning("Report PostgreSQL store unavailable, falling back to JSON: %s", exc)
            except Exception:
                logger.warning("Report PostgreSQL store init failed, falling back to JSON", exc_info=True)

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    def _missions_path(self) -> Path:
        return self._dir / "missions.json"

    def _reports_path(self) -> Path:
        return self._dir / "reports.json"

    def _read_json(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _write_json(self, path: Path, data: list[dict[str, Any]]) -> None:
        self._ensure_dir()
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- Missions ---

    def list_missions(
        self,
        *,
        avatar_id: str | None = None,
        group_id: str | None = None,
        owner_user_id: str | None = None,
        limit: int = 50,
    ) -> list[Mission]:
        if self._missions_pg is not None:
            missions = []
            for item in self._missions_pg.list():
                try:
                    m = Mission(**item)
                    if avatar_id and m.avatar_id != avatar_id:
                        continue
                    if group_id is not None and (m.group_id or "") != group_id:
                        continue
                    if owner_user_id is not None and (m.owner_user_id or "") != owner_user_id:
                        continue
                    missions.append(m)
                except Exception:
                    continue
            missions.sort(key=lambda m: m.started_at, reverse=True)
            return missions[:limit]

        items = self._read_json(self._missions_path())
        missions = []
        for item in items:
            try:
                m = Mission(**item)
                if avatar_id and m.avatar_id != avatar_id:
                    continue
                if group_id is not None and (m.group_id or "") != group_id:
                    continue
                if owner_user_id is not None and (m.owner_user_id or "") != owner_user_id:
                    continue
                missions.append(m)
            except Exception:
                continue
        missions.sort(key=lambda m: m.started_at, reverse=True)
        return missions[:limit]

    def get_mission(self, mission_id: str) -> Mission | None:
        for m in self.list_missions(limit=1000):
            if m.id == mission_id:
                return m
        return None

    def save_mission(self, mission: Mission) -> None:
        if self._missions_pg is not None:
            self._missions_pg.save(
                mission.id,
                mission.model_dump(),
                group_id=mission.group_id or "",
                owner_user_id=mission.owner_user_id or "",
            )
            return

        missions = self.list_missions(limit=10000)
        found = False
        for i, m in enumerate(missions):
            if m.id == mission.id:
                missions[i] = mission
                found = True
                break
        if not found:
            missions.append(mission)
        self._write_json(self._missions_path(), [m.model_dump() for m in missions])

    def delete_mission(self, mission_id: str) -> bool:
        if self._missions_pg is not None:
            return self._missions_pg.delete(mission_id)

        missions = self.list_missions(limit=10000)
        kept = [m for m in missions if m.id != mission_id]
        if len(kept) == len(missions):
            return False
        self._write_json(self._missions_path(), [m.model_dump() for m in kept])
        return True

    def delete_missions_for_avatar(self, avatar_id: str) -> int:
        missions = self.list_missions(limit=10000)
        kept = [m for m in missions if m.avatar_id != avatar_id]
        removed = len(missions) - len(kept)
        if removed:
            if self._missions_pg is not None:
                for m in missions:
                    if m.avatar_id == avatar_id:
                        self._missions_pg.delete(m.id)
            else:
                self._write_json(self._missions_path(), [m.model_dump() for m in kept])
        return removed

    def delete_reports_for_avatar(self, avatar_id: str) -> int:
        reports = self.list_reports(limit=10000)
        kept = [r for r in reports if r.avatar_id != avatar_id]
        removed = len(reports) - len(kept)
        if removed:
            if self._reports_pg is not None:
                for r in reports:
                    if r.avatar_id == avatar_id:
                        self._reports_pg.delete(r.id)
            else:
                self._write_json(self._reports_path(), [r.model_dump() for r in kept])
        return removed

    # --- Reports ---

    def list_reports(
        self,
        *,
        avatar_id: str | None = None,
        group_id: str | None = None,
        owner_user_id: str | None = None,
        limit: int = 50,
    ) -> list[MissionReport]:
        if self._reports_pg is not None:
            reports = []
            for item in self._reports_pg.list():
                try:
                    r = MissionReport(**item)
                    if avatar_id and r.avatar_id != avatar_id:
                        continue
                    if group_id is not None and (r.group_id or "") != group_id:
                        continue
                    if owner_user_id is not None and (r.owner_user_id or "") != owner_user_id:
                        continue
                    reports.append(r)
                except Exception:
                    continue
            reports.sort(key=lambda r: r.created_at, reverse=True)
            return reports[:limit]

        items = self._read_json(self._reports_path())
        reports = []
        for item in items:
            try:
                r = MissionReport(**item)
                if avatar_id and r.avatar_id != avatar_id:
                    continue
                if group_id is not None and (r.group_id or "") != group_id:
                    continue
                if owner_user_id is not None and (r.owner_user_id or "") != owner_user_id:
                    continue
                reports.append(r)
            except Exception:
                continue
        reports.sort(key=lambda r: r.created_at, reverse=True)
        return reports[:limit]

    def get_report(self, report_id: str) -> MissionReport | None:
        for r in self.list_reports(limit=10000):
            if r.id == report_id:
                return r
        return None

    def save_report(self, report: MissionReport) -> None:
        if self._reports_pg is not None:
            self._reports_pg.save(
                report.id,
                report.model_dump(),
                group_id=report.group_id or "",
                owner_user_id=report.owner_user_id or "",
            )
            return

        reports = self.list_reports(limit=10000)
        found = False
        for i, r in enumerate(reports):
            if r.id == report.id:
                reports[i] = report
                found = True
                break
        if not found:
            reports.append(report)
        self._write_json(self._reports_path(), [r.model_dump() for r in reports])
