from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from jiuwenclaw.extensions.redis.redis_keys import cron_jobs_hash_rel
from jiuwenclaw.extensions.redis.redis_client import RedisClient
from jiuwenclaw.gateway.cron.cron_job_mutations import apply_cron_job_patch, build_new_cron_job, sort_cron_jobs
from jiuwenclaw.gateway.cron.models import CronJob

logger = logging.getLogger(__name__)


class RedisCronJobStore:
    """Cron jobs in Redis Hash (active-standby Gateway, scoped by ``gateway.instance_id``).

    变更由本机 ``CronController`` 写后 ``scheduler.reload()`` 感知；不维护 rev / Pub/Sub。
    """

    def __init__(self, client: RedisClient, *, gateway_instance_id: str) -> None:
        self._client = client
        self._gateway_instance_id = str(gateway_instance_id or "").strip()
        self._jobs_hash_rel = cron_jobs_hash_rel(self._gateway_instance_id)
        self._lock = asyncio.Lock()

    async def get_revision(self) -> int:
        """主备单活：不维护 Redis revision，轮询不会据此 reload。"""
        return 0

    async def list_jobs(self) -> list[CronJob]:
        raw = await self._client.hgetall(self._jobs_hash_rel)
        jobs: list[CronJob] = []
        for field, value in (raw or {}).items():
            if not isinstance(value, str) or not value.strip():
                continue
            try:
                item = json.loads(value)
                if isinstance(item, dict):
                    jobs.append(CronJob.from_dict(item))
            except Exception:
                logger.debug("[RedisCronJobStore] skip invalid field=%s", field)
                continue
        return sort_cron_jobs(jobs)

    async def get_job(self, job_id: str) -> CronJob | None:
        job_id = str(job_id or "").strip()
        if not job_id:
            return None
        raw = await self._client.hget(self._jobs_hash_rel, job_id)
        if not raw or not str(raw).strip():
            return None
        try:
            data = json.loads(str(raw))
            if isinstance(data, dict):
                return CronJob.from_dict(data)
        except Exception:
            return None
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
        async with self._lock:
            await self._client.hset(
                self._jobs_hash_rel,
                job.id,
                json.dumps(job.to_dict(), ensure_ascii=False),
            )
        return job

    async def update_job(self, job_id: str, patch: dict[str, Any]) -> CronJob:
        job_id = str(job_id or "").strip()
        if not job_id:
            raise ValueError("id is required")
        existing = await self.get_job(job_id)
        if existing is None:
            raise KeyError("job not found")
        updated = apply_cron_job_patch(existing, patch)
        async with self._lock:
            await self._client.hset(
                self._jobs_hash_rel,
                job_id,
                json.dumps(updated.to_dict(), ensure_ascii=False),
            )
        return updated

    async def delete_job(self, job_id: str) -> bool:
        job_id = str(job_id or "").strip()
        if not job_id:
            return False
        async with self._lock:
            existed = await self.get_job(job_id) is not None
            if not existed:
                return False
            await self._client.hdel(self._jobs_hash_rel, job_id)
            return True
