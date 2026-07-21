from __future__ import annotations

import logging

from jiuwenclaw.extensions.redis import (
    get_declared_deployment_mode,
    get_effective_distributed_redis_active,
    get_gateway_instance_id,
    get_gateway_redis_client,
)
from jiuwenclaw.gateway.cron.enterprise_gate import is_enterprise_edition
from jiuwenclaw.gateway.cron.redis_store import RedisCronJobStore
from jiuwenclaw.gateway.cron.store import FileCronJobStore
from jiuwenclaw.gateway.cron.store_base import CronJobStoreBackend
from jiuwenclaw.utils import get_user_workspace_dir

logger = logging.getLogger(__name__)


async def _create_non_enterprise_cron_store() -> CronJobStoreBackend:
    mode = get_declared_deployment_mode()
    if mode == "standalone":
        path = get_user_workspace_dir() / "gateway" / "cron_jobs.json"
        return FileCronJobStore(path=path)
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


async def create_gateway_cron_store() -> CronJobStoreBackend:
    """创建 Gateway cron store。

    - 非企业版：直接返回 file（standalone）或 Redis（active-standby），
      **不**经过 ``EnterpriseAwareCronJobStore``。
    - 企业版（``AGENT_RUNTIME`` 非空）：包一层门面；``jiuwenclaw_id`` 绑定后走 DB，
      未 bind 时仍转发非企业 store。
    """
    non_enterprise = await _create_non_enterprise_cron_store()
    if not is_enterprise_edition():
        logger.info("[Cron] gateway cron store ready (non-enterprise, no facade)")
        return non_enterprise

    from jiuwenclaw.gateway.cron.enterprise_store import EnterpriseAwareCronJobStore

    store: CronJobStoreBackend = EnterpriseAwareCronJobStore(non_enterprise)
    logger.info("[Cron] gateway cron store ready (enterprise-aware facade)")
    return store
