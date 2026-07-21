# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway 实例生命周期：响应 Manager purge 指令并清理 GDB 实例级数据。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.db import ensure_db_handler
from ...infrastructure.utils import get_jiuwenclaw_id

logger = logging.getLogger(__name__)

_LIST_ALL_CAP = 10_000

# 按 jiuwenclaw_id 隔离的业务表（删除实例时整实例 purge）。
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
    "channel_config",
    "logging_config",
    "task_memory_config",
    "permissions_config",
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


async def apply_instance_data_lifecycle(payload: dict[str, Any]) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 instance_data_lifecycle 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("instance_data_lifecycle.op is required")

    jiuwenclaw_id = get_jiuwenclaw_id()
    if not jiuwenclaw_id:
        raise ValueError("jiuwenclaw_id is not set")

    if op == "purge":
        handler = await ensure_db_handler()
        counts = await purge_jiuwenclaw_instance_data_on_handler(handler, jiuwenclaw_id)
        logger.info(
            "[ManagerWsClient] instance_data_lifecycle purge jiuwenclaw_id=%s counts=%s",
            jiuwenclaw_id,
            counts,
        )
        return {"purged": counts}

    raise ValueError(f"unsupported instance_data_lifecycle.op: {op!r}")


__all__ = (
    "INSTANCE_PURGE_TABLES",
    "apply_instance_data_lifecycle",
    "purge_jiuwenclaw_instance_data_on_handler",
)
