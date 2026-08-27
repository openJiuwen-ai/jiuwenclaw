# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""TaskMemory 配置 WebSocket 同步：将 Claw Manager 下发的 task_memory_config 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from ...infrastructure.repository_access import require_enterprise_repository
from ...infrastructure.utils import format_ts, utc_now
from ...models.application_config_models import TASK_MEMORY_CONFIG_TABLE_DEF

_TABLE = TASK_MEMORY_CONFIG_TABLE_DEF.table_name
logger = logging.getLogger(__name__)


def _row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "jiuwenclaw_id": row.get("jiuwenclaw_id"),
        "enabled": row.get("enabled", False),
        "llm_model": row.get("llm_model", ""),
        "embedding_model": row.get("embedding_model", ""),
        "api_key": row.get("api_key", ""),
        "api_base": row.get("api_base", ""),
        "retrieval_algo": row.get("retrieval_algo", ""),
        "summary_algo": row.get("summary_algo", ""),
        "created_at": format_ts(row.get("created_at")),
        "updated_at": format_ts(row.get("updated_at")),
    }


async def _upsert_task_memory_config_record(
    request: dict[str, Any],
) -> dict[str, Any]:
    repo = require_enterprise_repository(_TABLE)
    existing = await repo.get()
    now = utc_now()
    if existing is not None:
        update_data: dict[str, Any] = {"updated_at": now}
        if "enabled" in request:
            update_data["enabled"] = bool(request.get("enabled", False))
        if request.get("llm_model") != "":
            update_data["llm_model"] = request.get("llm_model")
        if request.get("embedding_model") != "":
            update_data["embedding_model"] = request.get("embedding_model")
        if request.get("api_key") != "":
            update_data["api_key"] = request.get("api_key")
        if request.get("api_base") != "":
            update_data["api_base"] = request.get("api_base")
        if request.get("retrieval_algo") is not None:
            update_data["retrieval_algo"] = request.get("retrieval_algo")
        if request.get("summary_algo") is not None:
            update_data["summary_algo"] = request.get("summary_algo")
        updated = await repo.update({}, update_data)
        if updated is None:
            raise ValueError("failed to update task_memory config")
        return _row_to_dict(updated)

    row_data = {
        "enabled": bool(request.get("enabled", False)),
        "llm_model": request.get("llm_model"),
        "embedding_model": request.get("embedding_model"),
        "api_key": request.get("api_key"),
        "api_base": request.get("api_base"),
        "retrieval_algo": request.get("retrieval_algo", ""),
        "summary_algo": request.get("summary_algo", ""),
        "created_at": now,
        "updated_at": now,
    }
    created = await repo.create(row_data)
    return _row_to_dict(created)


async def apply_task_memory_config(payload: dict[str, Any]) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 task_memory_config 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("task_memory_config.op is required")

    repo = require_enterprise_repository(_TABLE)

    if op == "upsert":
        task_memory = payload.get("task_memory")
        if not isinstance(task_memory, dict):
            raise ValueError("task_memory_config.upsert requires task_memory object")
        result = await _upsert_task_memory_config_record(task_memory)

    elif op == "delete":
        await repo.delete()
        result = None
        logger.info("[ManagerWsClient] task_memory_config deleted")

    else:
        raise ValueError(f"unsupported task_memory_config.op: {op!r}")

    logger.info(
        "[ManagerWsClient] task_memory_config sync op=%s",
        op,
    )
    return result
