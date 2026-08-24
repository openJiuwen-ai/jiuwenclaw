# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Memory 配置 WebSocket 同步：写入 Gateway 本地库并热更新 AgentServer 缓存。"""

from __future__ import annotations

import logging
from typing import Any

from ...infrastructure.db import ensure_db_handler
from ...infrastructure.utils import format_ts, get_jiuwenclaw_id, utc_now
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
    from jiuwenclaw.agentserver.memory.config import (
        apply_memory_config_payload,
        is_enterprise_memory_config_enabled,
    )

    if not is_enterprise_memory_config_enabled():
        logger.debug(
            "[ManagerWsClient] skip memory_config hot-reload: not enterprise runtime"
        )
        return

    if op == "delete":
        apply_memory_config_payload({"op": "delete"})
        return
    apply_memory_config_payload({"body": body} if isinstance(body, dict) else None)


async def apply_memory_config(payload: dict[str, Any]) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 memory_config 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("memory_config.op is required")

    handler = await ensure_db_handler()
    jiuwenclaw_id = get_jiuwenclaw_id() or ""
    now = utc_now()
    result: dict[str, Any] | None = None

    if op == "upsert":
        body = payload.get("body")
        if not isinstance(body, dict):
            raise ValueError("memory_config.body must be an object for upsert")

        existing = await handler.get(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        if existing is not None:
            update_data: dict[str, Any] = {
                "body": body,
                "source": str(payload.get("source") or "manager"),
                "updated_at": now,
                "revision": int(getattr(existing, "revision", 1) or 1) + 1,
            }
            updated = await handler.update(
                _TABLE,
                {"jiuwenclaw_id": jiuwenclaw_id},
                update_data,
            )
            result = _row_to_dict(updated) if updated else None
        else:
            row_data = {
                "jiuwenclaw_id": jiuwenclaw_id,
                "body": body,
                "source": str(payload.get("source") or "manager"),
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
            created = await handler.create(_TABLE, row_data)
            result = _row_to_dict(created) if created else None

        _apply_memory(body, op=op)
        logger.info(
            "[ManagerWsClient] memory_config hot-reload upsert revision=%s",
            (result or {}).get("revision"),
        )

    elif op == "delete":
        await handler.delete(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        _apply_memory(None, op=op)
        result = None
        logger.info("[ManagerWsClient] memory_config deleted, reverted to yaml fallback")

    else:
        raise ValueError(f"unsupported memory_config.op: {op!r}")

    return result
