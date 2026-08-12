# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Mirror Gateway cron mutations to Agent tenant ``agent/home/cron_jobs.json``."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jiuwenswarm.common.utils import resolve_tenant_agent_root_dir
from jiuwenswarm.gateway.cron.store import CronJobStore

logger = logging.getLogger(__name__)


def _agent_cron_jobs_path(service_id: str, agent_id: str) -> Path:
    wk = "default" if (str(service_id or "default") == "default" and str(agent_id or "default") == "default") else f"{service_id}_{agent_id}"
    return resolve_tenant_agent_root_dir(wk) / "home" / "cron_jobs.json"


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
    deleted = await store.delete_job(job_id, force=True)
    logger.debug(
        "[CronMirror] delete job=%s deleted=%s service_id=%s agent_id=%s",
        job_id,
        deleted,
        service_id,
        agent_id,
    )
