from __future__ import annotations

import logging
import os
from typing import Any

from jiuwenswarm.common.utils import get_cron_jobs_path
from jiuwenswarm.extensions.redis import (
    get_declared_deployment_mode,
    get_effective_distributed_redis_active,
    get_gateway_instance_id,
    get_gateway_redis_client,
)
from jiuwenswarm.gateway.cron.enterprise_gate import is_enterprise_edition
from jiuwenswarm.gateway.cron.redis_store import RedisCronJobStore
from jiuwenswarm.gateway.cron.store import FileCronJobStore
from jiuwenswarm.gateway.cron.store_base import CronJobStoreBackend

logger = logging.getLogger(__name__)


async def _create_base_cron_store() -> CronJobStoreBackend:
    """Create cron store: file by default; Redis only under AGENT_RUNTIME + active-standby."""
    # 企业版特性：无 AGENT_RUNTIME 时始终使用本地文件 store
    if not os.getenv("AGENT_RUNTIME", "").strip():
        return FileCronJobStore(path=get_cron_jobs_path())

    mode = get_declared_deployment_mode()
    if mode == "standalone":
        return FileCronJobStore(path=get_cron_jobs_path())
    if not get_effective_distributed_redis_active():
        raise RuntimeError(
            "gateway.deployment_mode=active-standby requires Redis; "
            "connection failed or degraded. Fix redis config/connectivity."
        )
    client = get_gateway_redis_client()
    if client is None:
        raise RuntimeError("active-standby mode: Redis client is None")
    instance_id = get_gateway_instance_id()
    if not instance_id:
        raise RuntimeError(
            "active-standby mode: gateway.instance_id is required for Cron Redis store "
            "(set gateway.instance_id or GATEWAY_INSTANCE_ID before Gateway starts)"
        )
    return RedisCronJobStore(client, gateway_instance_id=instance_id)


async def create_gateway_cron_store() -> CronJobStoreBackend | Any:
    """Create Gateway cron store.

    - Non-enterprise: file store (or Redis under AGENT_RUNTIME + active-standby).
    - Enterprise (``AGENT_RUNTIME`` set): wrap base store; use Gateway DB after bind.
    """
    base = await _create_base_cron_store()
    if not is_enterprise_edition():
        logger.info("[Cron] gateway cron store ready (non-enterprise)")
        return base

    from jiuwenswarm.gateway.cron.enterprise_store import EnterpriseAwareCronJobStore

    store = EnterpriseAwareCronJobStore(base)
    logger.info("[Cron] gateway cron store ready (enterprise-aware facade)")
    return store
