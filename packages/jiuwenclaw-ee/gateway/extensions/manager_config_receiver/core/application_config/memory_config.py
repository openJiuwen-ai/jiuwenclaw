# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Memory 配置：写入 Gateway 本地库并热更新 AgentServer 缓存。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import format_ts, utc_now
from ...models.application_config_models import MEMORY_CONFIG_TABLE_DEF

_TABLE = MEMORY_CONFIG_TABLE_DEF.table_name
logger = logging.getLogger(__name__)


def _row_to_dict(obj: Any) -> dict[str, Any]:
    body = getattr(obj, "body", None)
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "body": dict(body) if isinstance(body, dict) else body,
        "source": getattr(obj, "source", "manager"),
        "revision": getattr(obj, "revision", 1),
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
    }


def _apply_memory(body: dict[str, Any] | None, *, op: str) -> None:
    from jiuwenswarm.agents.harness.common.memory.config import (
        apply_memory_config_payload,
        is_enterprise_memory_config_enabled,
    )

    if not is_enterprise_memory_config_enabled():
        logger.debug(
            "[ManagerConfigReceiver] skip memory_config hot-reload: not enterprise runtime"
        )
        return

    if op == "delete":
        apply_memory_config_payload({"op": "delete"})
        return
    apply_memory_config_payload({"body": body} if isinstance(body, dict) else None)


class MemoryConfigService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def upsert(
        self,
        jiuwenclaw_id: str,
        *,
        body: dict[str, Any] | None = None,
        source: str = "manager",
        **_extra: Any,
    ) -> dict[str, Any] | None:
        if body is None and isinstance(_extra.get("body"), dict):
            body = _extra["body"]
        if body is None:
            cand = {k: v for k, v in _extra.items() if k != "source"}
            if cand:
                body = cand
        if not isinstance(body, dict):
            raise ValueError("memory_config.body must be an object for upsert")

        now = utc_now()
        existing = await self._handler.get(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        if existing is not None:
            update_data: dict[str, Any] = {
                "body": body,
                "source": str(source or "manager"),
                "updated_at": now,
                "revision": int(getattr(existing, "revision", 1) or 1) + 1,
            }
            updated = await self._handler.update(
                _TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                update_data,
            )
            result = _row_to_dict(updated) if updated else None
        else:
            row_data = {
                "jiuwenclaw_id": jiuwenclaw_id,
                "body": body,
                "source": str(source or "manager"),
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
            created = await self._handler.create(_TABLE, row_data)
            result = _row_to_dict(created) if created else None

        _apply_memory(body, op="upsert")
        logger.info(
            "[ManagerConfigReceiver] memory_config hot-reload upsert revision=%s",
            (result or {}).get("revision"),
        )
        return result

    async def delete(self, jiuwenclaw_id: str) -> None:
        await self._handler.delete(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        _apply_memory(None, op="delete")
        logger.info(
            "[ManagerConfigReceiver] memory_config deleted jiuwenclaw_id=%s",
            jiuwenclaw_id,
        )
