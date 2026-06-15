from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from jiuwenclaw.gateway.cron.cron_job_mutations import apply_cron_job_patch, build_new_cron_job, sort_cron_jobs
from jiuwenclaw.gateway.cron.models import CronJob, CronTarget
from jiuwenclaw.utils import get_agent_home_dir


class FileCronJobStore:
    """Persist cron jobs to a local JSON file (standalone Gateway)."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (get_agent_home_dir() / "cron_jobs.json")
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def list_jobs(self) -> list[CronJob]:
        data = await self._read_json()
        jobs_raw = data.get("jobs") or []
        if not isinstance(jobs_raw, list):
            return []
        jobs: list[CronJob] = []
        for item in jobs_raw:
            if not isinstance(item, dict):
                continue
            try:
                jobs.append(CronJob.from_dict(item))
            except Exception:
                # Ignore invalid entries to keep system robust
                continue
        return sort_cron_jobs(jobs)

    async def get_job(self, job_id: str) -> CronJob | None:
        job_id = str(job_id or "").strip()
        if not job_id:
            return None
        for job in await self.list_jobs():
            if job.id == job_id:
                return job
        return None

    async def create_job(
        self,
        *,
        job_id: str | None = None,
        name: str,
        cron_expr: str,
        timezone: str,
        description: str,
        targets: str,
        enabled: bool = True,
        wake_offset_seconds: int | None = None,
        session_id: str | None = None,
        chat_type: str | None = None,
        mode: str | None = None,
        delete_after_run: bool | None = None,
    ) -> CronJob:
        job = build_new_cron_job(
            job_id=job_id,
            name=name,
            cron_expr=cron_expr,
            timezone=timezone,
            description=description,
            targets=targets,
            enabled=enabled,
            wake_offset_seconds=wake_offset_seconds,
            session_id=session_id,
            chat_type=chat_type,
            mode=mode,
            delete_after_run=delete_after_run,
        )
        await self._upsert_job(job)
        return job

    async def update_job(self, job_id: str, patch: dict[str, Any]) -> CronJob:
        job_id = str(job_id or "").strip()
        if not job_id:
            raise ValueError("id is required")
        patch = dict(patch or {})
        existing = await self.get_job(job_id)
        if existing is None:
            raise KeyError("job not found")
        updated = apply_cron_job_patch(existing, patch)
        await self._upsert_job(updated)
        return updated

    async def delete_job(self, job_id: str) -> bool:
        job_id = str(job_id or "").strip()
        if not job_id:
            return False
        async with self._lock:
            data = self._read_json_unlocked()
            jobs_raw = data.get("jobs") or []
            if not isinstance(jobs_raw, list):
                jobs_raw = []
            kept: list[dict[str, Any]] = []
            deleted = False
            for item in jobs_raw:
                if not isinstance(item, dict):
                    continue
                if str(item.get("id") or "").strip() == job_id:
                    deleted = True
                    continue
                kept.append(item)
            data["version"] = int(data.get("version") or 1)
            data["jobs"] = kept
            if deleted:
                self._write_json_unlocked(data)
            return deleted

    async def _upsert_job(self, job: CronJob) -> None:
        async with self._lock:
            data = self._read_json_unlocked()
            jobs_raw = data.get("jobs") or []
            if not isinstance(jobs_raw, list):
                jobs_raw = []
            out: list[dict[str, Any]] = []
            found = False
            for item in jobs_raw:
                if not isinstance(item, dict):
                    continue
                if str(item.get("id") or "").strip() == job.id:
                    out.append(job.to_dict())
                    found = True
                else:
                    out.append(item)
            if not found:
                out.append(job.to_dict())
            data["version"] = int(data.get("version") or 1)
            data["jobs"] = out
            self._write_json_unlocked(data)

    async def _read_json(self) -> dict[str, Any]:
        async with self._lock:
            return self._read_json_unlocked()

    def _read_json_unlocked(self) -> dict[str, Any]:
        path = self._path
        try:
            if not path.exists():
                return {"version": 1, "jobs": []}
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict):
                return {"version": 1, "jobs": []}
            if "version" not in data:
                data["version"] = 1
            if "jobs" not in data:
                data["jobs"] = []
            return data
        except Exception:
            return {"version": 1, "jobs": []}

    def _write_json_unlocked(self, data: dict[str, Any]) -> None:
        path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)

    async def get_revision(self) -> int:
        path = self._path
        try:
            if not path.exists():
                return 0
            return int(path.stat().st_mtime * 1_000_000)
        except OSError:
            return 0

    @staticmethod
    def _normalize_targets(targets: Any) -> list[CronTarget]:
        out: list[CronTarget] = []
        if isinstance(targets, list):
            for item in targets:
                if isinstance(item, CronTarget):
                    out.append(item)
                elif isinstance(item, dict):
                    out.append(CronTarget.from_dict(item))
        if not out:
            raise ValueError("targets is required")
        return out


CronJobStore = FileCronJobStore
