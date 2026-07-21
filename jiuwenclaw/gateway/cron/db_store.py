"""Gateway DB-backed cron job store（企业就绪路径唯一权威存储）。

仅 Gateway 进程写入；依赖 EE ``manager_ws_client`` 的 DBHandler / GatewayDb。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone as dt_timezone
from typing import Any
from zoneinfo import ZoneInfo

from jiuwenclaw.gateway.cron.cron_expr import _DEFAULT_WAKE_OFFSET_SECONDS
from jiuwenclaw.gateway.cron.cron_job_mutations import apply_cron_job_patch, build_new_cron_job, sort_cron_jobs
from jiuwenclaw.gateway.cron.enterprise_gate import get_bound_jiuwenclaw_id
from jiuwenclaw.gateway.cron.models import CronJob
from jiuwenclaw.infrastructure.module_importer import import_manager_ws_client_module

logger = logging.getLogger(__name__)

_TABLE = "cron_job"


def _utc_now() -> datetime:
    return datetime.now(dt_timezone.utc)


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


def _compute_next_run_at(job: CronJob) -> datetime | None:
    try:
        from jiuwenclaw.gateway.cron.scheduler import _cron_next_push_dt

        tz = ZoneInfo(job.timezone or "Asia/Shanghai")
        base = datetime.now(tz=tz)
        return _cron_next_push_dt(job.cron_expr, base)
    except Exception:
        return None


_CRON_JOB_ROW_KEYS = (
    "job_id",
    "name",
    "enabled",
    "expired",
    "cron_expr",
    "timezone",
    "wake_offset_seconds",
    "description",
    "targets",
    "session_id",
    "chat_type",
    "mode",
    "delete_after_run",
    "group_id",
    "bot_id",
    "user_id",
    "created_at",
    "updated_at",
    "data",
)


def _record_to_mapping(row: Any) -> dict[str, Any]:
    """Normalize ORM / dict cron_job rows for ``_row_to_job``.

    ``openjiuwen_runtime`` SQLAlchemy records expose ``to_dict()`` but are not
    dataclasses and often have empty ``__annotations__``, so attribute probing
    via annotations yields ``{}`` and previously dropped every job on reload.
    """
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
    fields = getattr(row, "__dataclass_fields__", None) or getattr(row, "__annotations__", {})
    if fields:
        return {k: getattr(row, k) for k in fields if not str(k).startswith("_")}
    return {k: getattr(row, k) for k in _CRON_JOB_ROW_KEYS if hasattr(row, k)}


def _row_to_job(row: Any) -> CronJob | None:
    try:
        data = _record_to_mapping(row)
        if not data:
            raise ValueError("empty cron_job row mapping")

        extra = data.get("data") if isinstance(data.get("data"), dict) else {}
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
        if extra:
            for key in ("session_id", "chat_type", "mode"):
                if not job_dict.get(key) and extra.get(key):
                    job_dict[key] = extra.get(key)
        return CronJob.from_dict(job_dict)
    except Exception as exc:
        logger.debug("[GatewayDbCronJobStore] skip invalid row: %s", exc)
        return None


class GatewayDbCronJobStore:
    """企业 cron 唯一权威：Gateway DB ``cron_job`` 表。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._revision = 0

    @staticmethod
    def _require_jiuwenclaw_id() -> str:
        jid = get_bound_jiuwenclaw_id()
        if not jid:
            raise RuntimeError("enterprise cron requires bound jiuwenclaw_id")
        return jid

    async def _handler(self) -> Any:
        db_mod = import_manager_ws_client_module("infrastructure.db")
        return await db_mod.ensure_db_handler(log_prefix="cron_job")

    async def get_revision(self) -> int:
        return int(self._revision)

    def _bump_revision(self) -> None:
        self._revision = int(time.time() * 1_000_000)

    async def list_jobs(self, *, filters: dict[str, Any] | None = None) -> list[CronJob]:
        jid = self._require_jiuwenclaw_id()
        query: dict[str, Any] = {"jiuwenclaw_id": jid}
        filters = dict(filters or {})
        for key in ("group_id", "bot_id", "user_id"):
            val = filters.get(key)
            if isinstance(val, str) and val.strip():
                query[key] = val.strip()
        handler = await self._handler()
        rows = await handler.list_records(
            _TABLE,
            query,
            limit=10_000,
            offset=0,
            order_by=[("updated_at", True)],
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
        handler = await self._handler()
        row = await handler.get(_TABLE, {"jiuwenclaw_id": jid, "job_id": job_id})
        return _row_to_job(row) if row is not None else None

    @staticmethod
    def _job_to_row(job: CronJob, *, jiuwenclaw_id: str, existing: Any | None = None) -> dict[str, Any]:
        utils = import_manager_ws_client_module("infrastructure.utils")
        now = utils.utc_now() if hasattr(utils, "utc_now") else _utc_now()
        created_at = _epoch_to_dt(job.created_at) or (
            getattr(existing, "created_at", None) if existing is not None else now
        )
        next_run = _compute_next_run_at(job)
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
            "enabled": bool(job.enabled),
            "expired": bool(job.expired),
            "delete_after_run": bool(job.delete_after_run),
            "mode": job.mode or "agent",
            "targets": job.targets,
            "session_id": job.session_id,
            "chat_type": job.chat_type,
            "next_run_at": next_run,
            "created_at": created_at,
            "updated_at": now,
            "data": None,
        }

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
        group_id: str | None = None,
        bot_id: str | None = None,
        user_id: str | None = None,
    ) -> CronJob:
        jid = self._require_jiuwenclaw_id()
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
            group_id=group_id,
            bot_id=bot_id,
            user_id=user_id,
        )
        row_data = self._job_to_row(job, jiuwenclaw_id=jid)
        async with self._lock:
            handler = await self._handler()
            existing = await handler.get(_TABLE, {"jiuwenclaw_id": jid, "job_id": job.id})
            if existing is not None:
                raise ValueError(f"cron job already exists: {job.id}")
            created = await handler.create(_TABLE, row_data)
            if created is None:
                raise RuntimeError("failed to insert cron_job")
            self._bump_revision()
        return job

    async def update_job(self, job_id: str, patch: dict[str, Any]) -> CronJob:
        job_id = str(job_id or "").strip()
        if not job_id:
            raise ValueError("id is required")
        jid = self._require_jiuwenclaw_id()
        async with self._lock:
            handler = await self._handler()
            existing_row = await handler.get(_TABLE, {"jiuwenclaw_id": jid, "job_id": job_id})
            if existing_row is None:
                raise KeyError("job not found")
            existing = _row_to_job(existing_row)
            if existing is None:
                raise KeyError("job not found")
            updated = apply_cron_job_patch(existing, patch)
            row_data = self._job_to_row(updated, jiuwenclaw_id=jid, existing=existing_row)
            # last_run_at 由系统 patch 可选写入
            if "last_run_at" in patch:
                row_data["last_run_at"] = patch.get("last_run_at")
            else:
                row_data.pop("created_at", None)
            update_payload = dict(row_data)
            update_payload.pop("jiuwenclaw_id", None)
            update_payload.pop("job_id", None)
            update_payload.pop("created_at", None)
            result = await handler.update(
                _TABLE,
                {"jiuwenclaw_id": jid, "job_id": job_id},
                update_payload,
            )
            if result is None:
                raise RuntimeError("failed to update cron_job")
            self._bump_revision()
        return updated

    async def delete_job(self, job_id: str) -> bool:
        job_id = str(job_id or "").strip()
        if not job_id:
            return False
        jid = self._require_jiuwenclaw_id()
        async with self._lock:
            handler = await self._handler()
            deleted = await handler.delete(_TABLE, {"jiuwenclaw_id": jid, "job_id": job_id})
            if deleted:
                self._bump_revision()
            return bool(deleted)
