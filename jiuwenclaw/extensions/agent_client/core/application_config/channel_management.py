# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Channel 配置（channel_config）持久化：基于 ``DBHandler`` 异步读写。

应用启动时由 ``extensions.agent_client.app`` 的 lifespan 完成 ``connect`` 与
``init_table(CHANNEL_CONFIG_TABLE_DEF)``。
"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw.extensions.agent_client.core.utils import format_ts, utc_now
from jiuwenclaw.extensions.agent_client.models.application_config_models import (
    CHANNEL_CONFIG_TABLE_DEF,
)
from jiuwenclaw.extensions.agent_client.schemas.application_config_schemas import (
    ChannelConfigCreateRequest,
)


def _channel_row_to_dict(obj: Any) -> dict[str, Any]:
    raw_cfg = getattr(obj, "config", None)
    full = dict(raw_cfg) if isinstance(raw_cfg, dict) else {}
    extra = full.pop("data", None)
    return {
        "id": getattr(obj, "id"),
        "channel_id": getattr(obj, "channel_id"),
        "channel_name": getattr(obj, "channel_name"),
        "channel_type": getattr(obj, "channel_type"),
        "bot_id": getattr(obj, "bot_id"),
        "config": full,
        "status": str(getattr(obj, "status")),
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
        "data": extra if isinstance(extra, dict) else extra,
    }


async def create_channel_config_record(
    handler: DBHandler,
    request: ChannelConfigCreateRequest,
) -> dict[str, Any]:
    dup = await handler.get(
        CHANNEL_CONFIG_TABLE_DEF.table_name,
        {"channel_id": request.channel_id},
    )
    if dup is not None:
        raise ValueError("channel_id already exists")
    now = utc_now()
    row_data: dict[str, Any] = {
        "channel_id": request.channel_id,
        "channel_name": request.channel_name,
        "channel_type": request.channel_type,
        "bot_id": request.bot_id,
        "config": request.config,
        "status": request.status,
        "created_at": now,
        "updated_at": now,
    }
    record = await handler.create(CHANNEL_CONFIG_TABLE_DEF.table_name, row_data)
    return _channel_row_to_dict(record)


async def list_channel_config_records(
    handler: DBHandler,
    channel_type: str | None = None,
    status: str | None = None,
    *,
    page_size: int = 10,
    page_num: int = 1,
) -> dict[str, Any]:
    """分页列出 channel 配置；``limit=page_size``，``offset=(page_num-1)*page_size``。"""
    filters: dict[str, Any] = {}
    if channel_type:
        filters["channel_type"] = channel_type
    if status:
        filters["status"] = status
    limit = page_size
    offset = (page_num - 1) * page_size
    rows = await handler.list_records(
        CHANNEL_CONFIG_TABLE_DEF.table_name,
        filters,
        limit=limit,
        offset=offset,
    )
    total = await handler.count_records(
        CHANNEL_CONFIG_TABLE_DEF.table_name,
        filters,
    )
    items = [_channel_row_to_dict(r) for r in rows]
    return {"items": items, "total": total}


async def update_channel_config_record(
    handler: DBHandler,
    channel_id: str,
    patch: dict[str, Any],
) -> dict[str, Any] | None:
    """按 ``channel_id`` 更新 ``CHANNEL_CONFIG_TABLE_DEF``：写入 ``patch`` 并刷新 ``updated_at``。

    不存在或更新后读回失败时返回 ``None``。
    """
    row = await handler.get(
        CHANNEL_CONFIG_TABLE_DEF.table_name,
        {"channel_id": channel_id},
    )
    if row is None:
        return None
    now = utc_now()
    updated = await handler.update(
        CHANNEL_CONFIG_TABLE_DEF.table_name,
        {"channel_id": channel_id},
        {**patch, "updated_at": now},
    )
    if updated is None:
        return None
    return _channel_row_to_dict(updated)


async def set_channel_status(
    handler: DBHandler,
    channel_id: str,
    target_status: str,
) -> dict[str, Any] | None:
    return await update_channel_config_record(
        handler, channel_id, {"status": target_status}
    )


async def delete_channel_config_record(handler: DBHandler, channel_id: str) -> bool:
    return await handler.delete(
        CHANNEL_CONFIG_TABLE_DEF.table_name,
        {"channel_id": channel_id},
    )
