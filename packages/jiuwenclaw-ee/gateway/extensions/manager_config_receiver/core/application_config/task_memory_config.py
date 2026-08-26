# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""TaskMemory 配置：写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import format_ts, utc_now
from ...models.application_config_models import TASK_MEMORY_CONFIG_TABLE_DEF

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


class TaskMemoryConfigService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def upsert(
        self,
        jiuwenclaw_id: str,
        request: dict[str, Any] | None = None,
        **fields: Any,
    ) -> dict[str, Any] | None:
        payload = dict(request or {})
        payload.update({k: v for k, v in fields.items() if v is not None or k in fields})
        # HTTP body 可能直接摊平字段，也可能嵌套 task_memory
        if isinstance(payload.get("task_memory"), dict):
            payload = dict(payload["task_memory"])

        existing = await self._handler.get(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        now = utc_now()
        if existing is not None:
            update_data: dict[str, Any] = {"updated_at": now}
            if "enabled" in payload:
                update_data["enabled"] = bool(payload.get("enabled", False))
            if payload.get("llm_model") != "":
                update_data["llm_model"] = payload.get("llm_model")
            if payload.get("embedding_model") != "":
                update_data["embedding_model"] = payload.get("embedding_model")
            if payload.get("api_key") != "":
                update_data["api_key"] = payload.get("api_key")
            if payload.get("api_base") != "":
                update_data["api_base"] = payload.get("api_base")
            if payload.get("retrieval_algo") is not None:
                update_data["retrieval_algo"] = payload.get("retrieval_algo")
            if payload.get("summary_algo") is not None:
                update_data["summary_algo"] = payload.get("summary_algo")
            updated = await self._handler.update(
                _TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                update_data,
            )
            if updated is None:
                raise ValueError("failed to update task_memory config")
            return _task_memory_row_to_dict(updated)

        row_data = {
            "jiuwenclaw_id": jiuwenclaw_id,
            "enabled": bool(payload.get("enabled", False)),
            "llm_model": payload.get("llm_model"),
            "embedding_model": payload.get("embedding_model"),
            "api_key": payload.get("api_key"),
            "api_base": payload.get("api_base"),
            "retrieval_algo": payload.get("retrieval_algo", ""),
            "summary_algo": payload.get("summary_algo", ""),
            "created_at": now,
            "updated_at": now,
        }
        created = await self._handler.create(_TABLE, row_data)
        return _task_memory_row_to_dict(created) if created else None

    async def delete(self, jiuwenclaw_id: str) -> None:
        await self._handler.delete(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        logger.info(
            "[ManagerConfigReceiver] task_memory_config deleted jiuwenclaw_id=%s",
            jiuwenclaw_id,
        )
