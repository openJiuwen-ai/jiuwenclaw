# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Embed 配置 WebSocket 同步：将 Claw Manager 下发的 embed_config 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import format_ts, utc_now
from ...models.application_config_models import EMBED_CONFIG_TABLE_DEF
from ...infrastructure.db import ensure_db_handler

_TABLE = EMBED_CONFIG_TABLE_DEF.table_name
logger = logging.getLogger(__name__)


def _embed_row_to_dict(obj: Any) -> dict[str, Any]:
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "embed_api_key": getattr(obj, "embed_api_key", ""),
        "embed_base_url": getattr(obj, "embed_base_url", ""),
        "embed_model": getattr(obj, "embed_model", ""),
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
    }


async def _upsert_embed_config_record(
        handler: DBHandler,
        request: dict[str, Any],
) -> dict[str, Any]:
    from ...infrastructure.utils import get_jiuwenclaw_id
    jiuwenclaw_id = get_jiuwenclaw_id() or ""
    existing = await handler.get(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
    now = utc_now()
    if existing is not None:
        update_data: dict[str, Any] = {"updated_at": now}
        if request.get("embed_api_key") != "":
            update_data["embed_api_key"] = request.get("embed_api_key")
        if request.get("embed_base_url") != "":
            update_data["embed_base_url"] = request.get("embed_base_url")
        if request.get("embed_model") != "":
            update_data["embed_model"] = request.get("embed_model")
        updated = await handler.update(
            _TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
            update_data,
        )
        if updated is None:
            raise ValueError("failed to update embed config")
        return _embed_row_to_dict(updated) if updated else None
    else:
        row_data = {
            "jiuwenclaw_id": jiuwenclaw_id,
            "embed_api_key": request.get("embed_api_key"),
            "embed_base_url": request.get("embed_base_url"),
            "embed_model": request.get("embed_model"),
            "created_at": now,
            "updated_at": now,
        }
        created = await handler.create(_TABLE, row_data)
        return _embed_row_to_dict(created) if created else None


async def apply_embed_config(payload: dict[str, Any]) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 embed_config 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("embed_config.op is required")

    handler = await ensure_db_handler()

    if op == "upsert":
        embed = payload.get("embed")
        if not isinstance(embed, dict):
            raise ValueError("embed_config.upsert requires embed object")
        result = await _upsert_embed_config_record(handler, embed)

    elif op == "delete":
        from ...infrastructure.utils import get_jiuwenclaw_id
        jiuwenclaw_id = get_jiuwenclaw_id() or ""
        await handler.delete(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
        result = None
        logger.info("[ManagerWsClient] embed_config deleted")

    else:
        raise ValueError(f"unsupported embed_config.op: {op!r}")

    logger.info(
        "[ManagerWsClient] embed_config sync op=%s",
        op,
    )
    return result
