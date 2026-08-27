# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway 实例生命周期：响应 Manager purge 指令并清理 GDB 实例级数据。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenswarm.gateway.config.enterprise.repository import EnterpriseRecordRepository

from ...infrastructure.repository_access import (
    require_channel_repository,
    require_cron_job_enterprise_repository,
    require_enterprise_repository,
    require_logging_repository,
    require_permissions_repository,
)

logger = logging.getLogger(__name__)

_LIST_ALL_CAP = 10_000

INSTANCE_PURGE_TABLES: tuple[str, ...] = (
    "model_template",
    "embedding_template",
    "extension_config_template",
    "skill_whitelist_template",
    "service_config_template",
    "config_effective_global_policy",
    "config_effective_service_policy",
    "config_effective_agent_policy",
    "config_default_template_mapping",
    "log_masking_rule",
    "logging_config",
    "task_memory_config",
    "permissions_config",
    "memory_config",
    "cron_job",
)

_MANAGER_SIGN_PUBKEY_TABLE = "manager_sign_pubkey"

_EXCLUDED_FROM_ENTERPRISE_BULK_PURGE: frozenset[str] = frozenset({
    "channel_config",
    "logging_config",
    "permissions_config",
    "cron_job",
    "task_memory_config",
})

_enterprise_purge_tables: list[str] = []
for _table in INSTANCE_PURGE_TABLES:
    if _table not in _EXCLUDED_FROM_ENTERPRISE_BULK_PURGE:
        _enterprise_purge_tables.append(_table)
_ENTERPRISE_PURGE_TABLES: frozenset[str] = frozenset(_enterprise_purge_tables)


async def _purge_cron_job_table() -> int:
    return await _purge_enterprise_repository(
        require_cron_job_enterprise_repository()
    )


async def _purge_enterprise_table(table: str) -> int:
    return await _purge_enterprise_repository(require_enterprise_repository(table))


async def _purge_enterprise_repository(repo: EnterpriseRecordRepository) -> int:
    key_fields = repo.key_fields
    if not key_fields:
        return 1 if await repo.delete() else 0

    deleted = 0
    for row in await repo.list(limit=_LIST_ALL_CAP):
        key_parts = {field: row[field] for field in key_fields if field in row}
        if len(key_parts) != len(key_fields):
            continue
        if await repo.delete(key_parts):
            deleted += 1
    return deleted


async def _purge_channel_config() -> int:
    repo = require_channel_repository()
    deleted = 0
    for config in await repo.list(limit=_LIST_ALL_CAP):
        if await repo.delete(config.channel_id):
            deleted += 1
    return deleted


async def purge_jiuwenclaw_instance_data_on_handler(
    jiuwenclaw_id: str,
) -> dict[str, int]:
    """删除 Gateway 本地库中指定 ``jiuwenclaw_id`` 的全部实例数据（幂等）。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return {}

    deleted_counts: dict[str, int] = {}

    for table in _ENTERPRISE_PURGE_TABLES:
        count = await _purge_enterprise_table(table)
        if count:
            deleted_counts[table] = count

    channel_count = await _purge_channel_config()
    if channel_count:
        deleted_counts["channel_config"] = channel_count

    if await require_logging_repository().delete():
        deleted_counts["logging_config"] = 1

    if await require_permissions_repository().delete():
        deleted_counts["permissions_config"] = 1

    if await require_enterprise_repository("task_memory_config").delete():
        deleted_counts["task_memory_config"] = 1

    cron_count = await _purge_cron_job_table()
    if cron_count:
        deleted_counts["cron_job"] = cron_count

    if await require_enterprise_repository(_MANAGER_SIGN_PUBKEY_TABLE).delete():
        deleted_counts[_MANAGER_SIGN_PUBKEY_TABLE] = 1

    logger.info(
        "[instance_data_lifecycle] purged gateway instance data jiuwenclaw_id=%s counts=%s",
        jid,
        deleted_counts,
    )
    return deleted_counts


class InstanceDataLifecycleService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def purge(self, jiuwenclaw_id: str) -> dict[str, Any]:
        counts = await purge_jiuwenclaw_instance_data_on_handler(jiuwenclaw_id)
        logger.info(
            "[ManagerConfigReceiver] instance_data_lifecycle purge jiuwenclaw_id=%s counts=%s",
            jiuwenclaw_id,
            counts,
        )
        return {"purged": counts}


__all__ = (
    "INSTANCE_PURGE_TABLES",
    "InstanceDataLifecycleService",
    "purge_jiuwenclaw_instance_data_on_handler",
)
