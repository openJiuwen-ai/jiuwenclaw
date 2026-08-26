# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Gateway DB-backed cron job store（企业就绪路径权威存储，经 PersistentStore）。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo

from jiuwenswarm.gateway.cron.cron_expr import _DEFAULT_WAKE_OFFSET_SECONDS
from jiuwenswarm.gateway.cron.cron_job_mutations import (
    apply_cron_job_patch,
    build_new_cron_job,
    sort_cron_jobs,
)
from jiuwenswarm.gateway.cron.enterprise_gate import get_bound_jiuwenclaw_id
from jiuwenswarm.gateway.cron.models import CronJob
from jiuwenswarm.gateway.storage.protocols.persistent import PersistentStore

logger = logging.getLogger(__name__)

_TABLE = "cron_job"

_EXTRA_DATA_KEYS = (
    "project_id",
    "work_mode",
    "model_name",
    "app_id",
    "timeout_seconds",
    "last_session_id",
)


def _utc_now() -> datetime:
    return datetime.now(dt_timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _epoch_to_dt(value: float | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=dt_timezone.utc)
    except Exception:
        return None


def _dt_to_epoch(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=dt_timezone.utc)
        return float(dt.timestamp())
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt_timezone.utc)
            return float(parsed.timestamp())
        except Exception:
            return None
    return None


def _compute_next_run_at(job: CronJob) -> str | None:
    try:
        from jiuwenswarm.gateway.cron.scheduler import _cron_next_push_dt

        tz = ZoneInfo(job.timezone or "Asia/Shanghai")
        base = datetime.now(tz=tz)
        nxt = _cron_next_push_dt(job.cron_expr, base)
        if nxt is None:
            return None
        dt = nxt if nxt.tzinfo is not None else nxt.replace(tzinfo=dt_timezone.utc)
        return dt.astimezone(dt_timezone.utc).isoformat()
    except Exception:
        return None


def _record_to_mapping(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    model_dump = getattr(row, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        if isinstance(dumped, dict):
            return dumped
    to_dict = getattr(row, "to_dict", None)
    if callable(to_dict):
        dumped = to_dict()
        if isinstance(dumped, dict):
            return dumped
    return dict(row) if hasattr(row, "keys") else {}


def _extra_from_job(job: CronJob) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    for key in _EXTRA_DATA_KEYS:
        value = getattr(job, key, None)
        if value is not None and value != "":
            extra[key] = value
    return extra


def _row_to_job(row: Any) -> CronJob | None:
    try:
        data = _record_to_mapping(row)
        if not data:
            raise ValueError("empty cron_job row mapping")

        extra = data.get("data") if isinstance(data.get("data"), dict) else {}
        if isinstance(data.get("data"), str) and data.get("data", "").strip():
            try:
                parsed = json.loads(data["data"])
                if isinstance(parsed, dict):
                    extra = parsed
            except json.JSONDecodeError:
                extra = {}

        job_dict: dict[str, Any] = {
            "id": str(data.get("job_id") or "").strip(),
            "name": str(data.get("name") or "").strip(),
            "enabled": bool(data.get("enabled", False)),
            "expired": bool(data.get("expired", False)),
            "cron_expr": str(data.get("cron_expr") or "").strip(),
            "timezone": str(data.get("timezone") or "").strip(),
            "wake_offset_seconds": int(data.get("wake_offset_seconds") or 60),
            "description": str(data.get("description") or ""),
            "targets": str(data.get("targets") or "").strip(),
            "session_id": data.get("session_id"),
            "chat_type": data.get("chat_type"),
            "mode": data.get("mode") or "agent",
            "delete_after_run": bool(data.get("delete_after_run", False)),
            "group_id": data.get("group_id"),
            "bot_id": data.get("bot_id"),
            "user_id": data.get("user_id"),
            "created_at": _dt_to_epoch(data.get("created_at")),
            "updated_at": _dt_to_epoch(data.get("updated_at")),
        }
        for key in _EXTRA_DATA_KEYS:
            if key in extra and extra.get(key) is not None:
                job_dict[key] = extra[key]
        return CronJob.from_dict(job_dict)
    except Exception as exc:
        logger.debug("[GatewayDbCronJobStore] skip invalid row: %s", exc)
        return None


class GatewayDbCronJobStore:
    """企业 cron 权威：``cron_job`` 表经 ``PersistentStore``。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._revision = 0

    @staticmethod
    def _require_jiuwenclaw_id() -> str:
        jid = get_bound_jiuwenclaw_id()
        if not jid:
            raise RuntimeError("enterprise cron requires bound jiuwenclaw_id")
        return jid

    @staticmethod
    async def _require_store() -> PersistentStore:
        from jiuwenswarm.gateway.storage.access import require_persistent_store

        return await require_persistent_store()

    @staticmethod
    def _job_identity(*, jiuwenclaw_id: str, job_id: str) -> dict[str, Any]:
        return {"jiuwenclaw_id": jiuwenclaw_id, "job_id": job_id}

    async def get_revision(self) -> int:
        return int(self._revision)

    def _bump_revision(self) -> None:
        self._revision = int(time.time() * 1_000_000)

    async def list_jobs(self, *, filters: dict[str, Any] | None = None) -> list[CronJob]:
        jid = self._require_jiuwenclaw_id()
        store = await self._require_store()
        query: dict[str, Any] = {"jiuwenclaw_id": jid}
        filters = dict(filters or {})
        for key in ("group_id", "bot_id", "user_id"):
            val = filters.get(key)
            if isinstance(val, str) and val.strip():
                query[key] = val.strip()
        rows = await store.list(
            _TABLE,
            filters=query,
            order_by="updated_at DESC",
        )
        jobs: list[CronJob] = []
        for row in rows or []:
            job = _row_to_job(row)
            if job is not None:
                jobs.append(job)
        return sort_cron_jobs(jobs)

    async def get_job(self, job_id: str) -> CronJob | None:
        job_id = str(job_id or "").strip()
        if not job_id:
            return None
        jid = self._require_jiuwenclaw_id()
        store = await self._require_store()
        rows = await store.list(
            _TABLE,
            filters=self._job_identity(jiuwenclaw_id=jid, job_id=job_id),
            limit=1,
        )
        if not rows:
            return None
        return _row_to_job(rows[0])

    @staticmethod
    def _job_to_row(job: CronJob, *, jiuwenclaw_id: str) -> dict[str, Any]:
        now_iso = _utc_now_iso()
        created_iso = (
            _epoch_to_dt(job.created_at).astimezone(dt_timezone.utc).isoformat()
            if job.created_at is not None
            else now_iso
        )
        extra = _extra_from_job(job)
        return {
            "jiuwenclaw_id": jiuwenclaw_id,
            "job_id": job.id,
            "group_id": job.group_id,
            "bot_id": job.bot_id,
            "user_id": job.user_id,
            "name": job.name,
            "description": job.description or None,
            "cron_expr": job.cron_expr,
            "timezone": job.timezone,
            "wake_offset_seconds": int(
                job.wake_offset_seconds
                if job.wake_offset_seconds is not None
                else _DEFAULT_WAKE_OFFSET_SECONDS
            ),
            "enabled": 1 if job.enabled else 0,
            "expired": 1 if job.expired else 0,
            "delete_after_run": 1 if job.delete_after_run else 0,
            "mode": job.mode or "agent",
            "targets": job.targets,
            "session_id": job.session_id,
            "chat_type": job.chat_type,
            "next_run_at": _compute_next_run_at(job),
            "created_at": created_iso,
            "updated_at": now_iso,
            "data": json.dumps(extra, ensure_ascii=False) if extra else None,
        }

    async def create_job(self, **kwargs: Any) -> CronJob:
        jid = self._require_jiuwenclaw_id()
        store = await self._require_store()
        job = build_new_cron_job(**kwargs)
        row_data = self._job_to_row(job, jiuwenclaw_id=jid)
        identity = self._job_identity(jiuwenclaw_id=jid, job_id=job.id)
        async with self._lock:
            existing_rows = await store.list(_TABLE, filters=identity, limit=1)
            if existing_rows:
                raise ValueError(f"cron job already exists: {job.id}")
            await store.create(_TABLE, row_data)
            self._bump_revision()
        return job

    async def update_job(self, job_id: str, patch: dict[str, Any]) -> CronJob:
        job_id = str(job_id or "").strip()
        if not job_id:
            raise ValueError("id is required")
        jid = self._require_jiuwenclaw_id()
        store = await self._require_store()
        identity = self._job_identity(jiuwenclaw_id=jid, job_id=job_id)
        async with self._lock:
            existing = await self.get_job(job_id)
            if existing is None:
                raise KeyError("job not found")
            updated = apply_cron_job_patch(existing, patch)
            row_data = self._job_to_row(updated, jiuwenclaw_id=jid)
            row_data.pop("jiuwenclaw_id", None)
            row_data.pop("job_id", None)
            row_data.pop("created_at", None)
            if "last_run_at" in patch:
                row_data["last_run_at"] = patch.get("last_run_at")
            result = await store.update(_TABLE, identity, row_data)
            if result is None:
                raise KeyError("job not found")
            self._bump_revision()
        return updated

    async def delete_job(self, job_id: str, *, force: bool = False) -> bool:  # noqa: ARG002
        job_id = str(job_id or "").strip()
        if not job_id:
            return False
        jid = self._require_jiuwenclaw_id()
        store = await self._require_store()
        identity = self._job_identity(jiuwenclaw_id=jid, job_id=job_id)
        async with self._lock:
            deleted = await store.delete(_TABLE, identity)
            if deleted:
                self._bump_revision()
            return bool(deleted)
