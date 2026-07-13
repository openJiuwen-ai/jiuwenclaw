from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from jiuwenswarm.gateway.cron.models import (
    CronJob,
    CronTarget,
    CRON_JOB_DEFAULT_MODE,
    normalize_cron_job_mode,
    normalize_cron_job_timeout_seconds,
)
from jiuwenswarm.common.utils import get_cron_jobs_path

logger = logging.getLogger(__name__)

# proactive.tick 是由 proactive_cron_sync 自动注册、由 config 开关驱动的任务。
# 其 name/enabled/description/wake_offset/targets/mode 均由系统/配置侧维护，
# update 时只允许改调度本身（cron_expr/timezone）；expired/updated_at 由调度器/内部写。
# 用 mode 判断（而非硬编码 id），避免依赖 id 字符串。
_PROACTIVE_TICK_MODE = "proactive.tick"
_PROACTIVE_UPDATE_ALLOWED_KEYS: frozenset[str] = frozenset(
    {"cron_expr", "timezone", "expired", "updated_at"}
)


class CronJobStore:
    """Persist cron jobs to ~/.jiuwenswarm/agent/home/cron_jobs.json."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or get_cron_jobs_path()
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
        jobs.sort(key=lambda j: (j.updated_at or 0.0, j.created_at or 0.0), reverse=True)
        return jobs

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
        timeout_seconds: int | None = None,
        app_id: str = "",
    ) -> CronJob:
        now = time.time()
        sid = str(session_id).strip() if isinstance(session_id, str) and session_id.strip() else None
        ct = str(chat_type).strip() if isinstance(chat_type, str) and chat_type.strip() else None
        m = normalize_cron_job_mode(mode) if mode is not None and str(mode).strip() else CRON_JOB_DEFAULT_MODE
        dar = bool(delete_after_run) if delete_after_run is not None else False
        timeout = (
            normalize_cron_job_timeout_seconds(timeout_seconds)
            if timeout_seconds is not None
            else None
        )
        job = CronJob(
            id=str(job_id or "").strip() or uuid.uuid4().hex,
            name=str(name or "").strip(),
            enabled=bool(enabled),
            cron_expr=str(cron_expr or "").strip(),
            timezone=str(timezone or "").strip(),
            wake_offset_seconds=int(wake_offset_seconds) if wake_offset_seconds is not None else 300,
            description=str(description or ""),
            targets=str(targets or "").strip(),
            session_id=sid,
            created_at=now,
            updated_at=now,
            chat_type=ct,
            mode=m,
            delete_after_run=dar,
            timeout_seconds=timeout,
            app_id=str(app_id or "").strip(),
        )
        # validate via round-trip
        CronJob.from_dict(job.to_dict())
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

        # proactive.tick job：只接受调度字段（cron_expr/timezone），其余字段一律丢弃，
        # 防止前端或其它调用方改 name/enabled/description/wake_offset/targets/mode 等，
        # 这些字段由 config 开关 / proactive_cron_sync / scheduler 统一维护。
        if str(getattr(existing, "mode", "") or "").strip().lower() == _PROACTIVE_TICK_MODE:
            dropped = [k for k in patch if k not in _PROACTIVE_UPDATE_ALLOWED_KEYS]
            if dropped:
                logger.warning(
                    "[CronStore] reject proactive.tick update fields on job=%s: %s (only %s allowed)",
                    job_id, ", ".join(dropped), ", ".join(sorted(_PROACTIVE_UPDATE_ALLOWED_KEYS)),
                )
                patch = {k: v for k, v in patch.items() if k in _PROACTIVE_UPDATE_ALLOWED_KEYS}

        updated = existing
        if "name" in patch:
            updated = replace(updated, name=str(patch.get("name") or "").strip())
        if "enabled" in patch:
            enabled_val = bool(patch.get("enabled"))
            updated = replace(updated, enabled=enabled_val)
            # Re-enabling a job implies it is no longer expired, unless caller explicitly sets expired.
            if enabled_val and "expired" not in patch:
                updated = replace(updated, expired=False)
        if "cron_expr" in patch:
            updated = replace(updated, cron_expr=str(patch.get("cron_expr") or "").strip())
            # Editing schedule implies it is no longer expired, unless caller explicitly sets expired.
            if "expired" not in patch:
                updated = replace(updated, expired=False)
        if "timezone" in patch:
            updated = replace(updated, timezone=str(patch.get("timezone") or "").strip())
        if "wake_offset_seconds" in patch:
            raw = patch.get("wake_offset_seconds")
            try:
                wos = int(raw)
            except Exception as exc:  # noqa: BLE001
                raise ValueError("wake_offset_seconds must be int") from exc
            updated = replace(updated, wake_offset_seconds=max(0, wos))
        if "description" in patch:
            updated = replace(updated, description=str(patch.get("description") or ""))
        if "targets" in patch:
            updated = replace(updated, targets=str(patch.get("targets") or "").strip())
        if "session_id" in patch:
            raw_sid = patch.get("session_id")
            new_sid = str(raw_sid).strip() if isinstance(raw_sid, str) and str(raw_sid).strip() else None
            updated = replace(updated, session_id=new_sid)
        if "chat_type" in patch:
            raw_ct = patch.get("chat_type")
            new_ct = str(raw_ct).strip() if isinstance(raw_ct, str) and str(raw_ct).strip() else None
            updated = replace(updated, chat_type=new_ct)
        if "expired" in patch:
            updated = replace(updated, expired=bool(patch.get("expired")))
        if "mode" in patch:
            updated = replace(updated, mode=normalize_cron_job_mode(patch.get("mode")))
        if "delete_after_run" in patch:
            updated = replace(updated, delete_after_run=bool(patch.get("delete_after_run")))
        if "timeout_seconds" in patch:
            raw_timeout = patch.get("timeout_seconds")
            if raw_timeout is None:
                updated = replace(updated, timeout_seconds=None)
            else:
                updated = replace(
                    updated,
                    timeout_seconds=normalize_cron_job_timeout_seconds(raw_timeout),
                )

        updated.updated_at = time.time()
        CronJob.from_dict(updated.to_dict())
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
