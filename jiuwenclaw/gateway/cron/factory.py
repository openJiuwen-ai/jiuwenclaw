from __future__ import annotations

from jiuwenclaw.extensions.redis import (
    get_declared_deployment_mode,
    get_effective_distributed_redis_active,
    get_gateway_instance_id,
    get_gateway_redis_client,
)
from jiuwenclaw.gateway.cron.redis_store import RedisCronJobStore
from jiuwenclaw.gateway.cron.store import FileCronJobStore
from jiuwenclaw.gateway.cron.store_base import CronJobStoreBackend
from jiuwenclaw.utils import get_user_workspace_dir


async def create_gateway_cron_store() -> CronJobStoreBackend:
    mode = get_declared_deployment_mode()
    if mode == "standalone":
        path = get_user_workspace_dir() / "gateway" / "cron_jobs.json"
        return FileCronJobStore(path=path)
    if not get_effective_distributed_redis_active():
        raise RuntimeError(
            "gateway.deployment_mode=distributed requires Redis; "
            "connection failed or degraded. Fix redis config/connectivity."
        )
    client = get_gateway_redis_client()
    if client is None:
        raise RuntimeError("distributed mode: Redis client is None")
    instance_id = get_gateway_instance_id()
    if not instance_id:
        raise RuntimeError(
            "distributed mode: gateway.instance_id is required for Cron Redis store "
            "(set gateway.instance_id or GATEWAY_INSTANCE_ID before Gateway starts)"
        )
    return RedisCronJobStore(client, gateway_instance_id=instance_id)
