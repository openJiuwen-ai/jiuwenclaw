"""Mirror Gateway cron mutations to Agent tenant ``agent/home/cron_jobs.json`` (4a)."""

from __future__ import annotations

import logging
from typing import Any

from pathlib import Path

from jiuwenclaw.gateway.cron.store import CronJobStore
from jiuwenclaw.utils import resolve_tenant_agent_root_dir

logger = logging.getLogger(__name__)


def _agent_cron_jobs_path(service_id: str, agent_id: str) -> Path:
    return resolve_tenant_agent_root_dir(service_id, agent_id) / "home" / "cron_jobs.json"


def _agent_store(service_id: str, agent_id: str) -> CronJobStore:
    return CronJobStore(path=_agent_cron_jobs_path(service_id, agent_id))


async def mirror_job_upsert(
    job: dict[str, Any],
    *,
    service_id: str,
    agent_id: str,
) -> None:
    data = dict(job or {})
    data.setdefault("service_id", service_id)
    data.setdefault("agent_id", agent_id)
    store = _agent_store(service_id, agent_id)
    await store.upsert_from_dict(data)
    logger.debug(
        "[CronMirror] upsert job=%s service_id=%s agent_id=%s path=%s",
        data.get("id"),
        service_id,
        agent_id,
        store.path,
    )


async def mirror_job_delete(
    job_id: str,
    *,
    service_id: str,
    agent_id: str,
) -> None:
    store = _agent_store(service_id, agent_id)
    deleted = await store.delete_job(job_id)
    logger.debug(
        "[CronMirror] delete job=%s deleted=%s service_id=%s agent_id=%s",
        job_id,
        deleted,
        service_id,
        agent_id,
    )
