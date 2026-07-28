# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""TaskMemory 配置 WebSocket 同步：将 Claw Manager 下发的 task_memory_config 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import format_ts, utc_now
from ...models.application_config_models import TASK_MEMORY_CONFIG_TABLE_DEF
from ...infrastructure.db import ensure_db_handler

_TABLE = TASK_MEMORY_CONFIG_TABLE_DEF.table_name
logger = logging.getLogger(__name__)


def _task_memory_row_to_dict(obj: Any) -> dict[str, Any]:
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "enabled": getattr(obj, "enabled", False),
        "llm_model": getattr(obj, "llm_model", ""),
        "embedding_model": getattr(obj, "embedding_model", ""),
        "api_key": getattr(obj, "api_key", ""),
        "api_base": getattr(obj, "api_base", ""),
        "retrieval_algo": getattr(obj, "retrieval_algo", ""),
        "summary_algo": getattr(obj, "summary_algo", ""),
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
    }


async def _upsert_task_memory_config_record(
        handler: DBHandler,
        request: dict[str, Any],
) -> dict[str, Any]:
    from ...infrastructure.utils import get_jiuwenclaw_id
    jiuwenclaw_id = get_jiuwenclaw_id() or ""
    existing = await handler.get(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
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
        updated = await handler.update(
            _TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
            update_data,
        )
        if updated is None:
            raise ValueError("failed to update task_memory config")
        return _task_memory_row_to_dict(updated) if updated else None
    else:
        row_data = {
            "jiuwenclaw_id": jiuwenclaw_id,
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
        created = await handler.create(_TABLE, row_data)
        return _task_memory_row_to_dict(created) if created else None


async def apply_task_memory_config(payload: dict[str, Any]) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 task_memory_config 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("task_memory_config.op is required")

    handler = await ensure_db_handler()

    if op == "upsert":
        task_memory = payload.get("task_memory")
        if not isinstance(task_memory, dict):
            raise ValueError("task_memory_config.upsert requires task_memory object")
        result = await _upsert_task_memory_config_record(handler, task_memory)

    elif op == "delete":
        from ...infrastructure.utils import get_jiuwenclaw_id
        jiuwenclaw_id = get_jiuwenclaw_id() or ""
        await handler.delete(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        result = None
        logger.info("[ManagerWsClient] task_memory_config deleted")

    else:
        raise ValueError(f"unsupported task_memory_config.op: {op!r}")

    logger.info(
        "[ManagerWsClient] task_memory_config sync op=%s",
        op,
    )
    return result
