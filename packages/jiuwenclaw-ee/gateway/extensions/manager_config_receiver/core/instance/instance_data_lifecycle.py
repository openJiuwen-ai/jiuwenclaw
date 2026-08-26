# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway 实例生命周期：响应 Manager purge 指令并清理 GDB 实例级数据。

对齐 Manager ``manager_server.core.instance.instance_data_lifecycle.purge_gateway_instance_data``：
删除实例时 Manager 调用本扩展 HTTP 接口
``POST /api/v1/instance-data-lifecycle``（body ``op=purge``；实例 id 取 ``JIUWENCLAW_ID``）。
"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

logger = logging.getLogger(__name__)

_LIST_ALL_CAP = 10_000

# 按 jiuwenclaw_id 隔离的业务表（删除实例时整实例 purge）。
# 与 table_init.ALL_TABLE_DEFINITIONS 中实例级表对齐；不含 gateway_*_keypair（本机密钥）。
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


async def _delete_rows_for_instance(
    handler: DBHandler,
    table: str,
    jiuwenclaw_id: str,
) -> int:
    rows = await handler.list_records(
        table,
        {"jiuwenclaw_id": jiuwenclaw_id},
        limit=_LIST_ALL_CAP,
        offset=0,
    )
    deleted = 0
    for row in rows:
        row_id = getattr(row, "id", None)
        if row_id is None:
            continue
        if await handler.delete(table, {"id": row_id}):
            deleted += 1
    return deleted


async def purge_jiuwenclaw_instance_data_on_handler(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, int]:
    """删除 Gateway 本地库中指定 ``jiuwenclaw_id`` 的全部实例数据（幂等）。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return {}

    deleted_counts: dict[str, int] = {}

    for table in INSTANCE_PURGE_TABLES:
        count = await _delete_rows_for_instance(handler, table, jid)
        if count:
            deleted_counts[table] = count

    if await handler.delete(_MANAGER_SIGN_PUBKEY_TABLE, {"jiuwenclaw_id": jid}):
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
        counts = await purge_jiuwenclaw_instance_data_on_handler(
            self._handler, jiuwenclaw_id
        )
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
