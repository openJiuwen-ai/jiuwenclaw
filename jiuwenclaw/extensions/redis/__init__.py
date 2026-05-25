# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""网关分布式 Redis 实现（内置于 ``jiuwenclaw.extensions``，与动态扩展目录无关）。"""

from jiuwenclaw.extensions.redis.redis_client import RedisClient, RedisConfig
from jiuwenclaw.extensions.redis.redis_keys import (
    cron_jobs_hash_key,
    cron_jobs_hash_rel,
    leader_lock_key,
    session_map_hash_key,
)
from jiuwenclaw.extensions.redis.redis_runtime import (
    get_declared_deployment_mode,
    get_effective_distributed_redis_active,
    get_gateway_instance_id,
    get_gateway_redis_client,
    init_gateway_redis_from_config,
    is_redis_degraded,
    shutdown_gateway_redis,
)

__all__ = [
    "RedisClient",
    "RedisConfig",
    "cron_jobs_hash_key",
    "cron_jobs_hash_rel",
    "get_declared_deployment_mode",
    "get_effective_distributed_redis_active",
    "get_gateway_instance_id",
    "get_gateway_redis_client",
    "init_gateway_redis_from_config",
    "is_redis_degraded",
    "leader_lock_key",
    "session_map_hash_key",
    "shutdown_gateway_redis",
]
