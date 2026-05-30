# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Channel 配置 WebSocket 同步：将 Claw Manager 下发的 channel_config 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw.gateway.channel_config_reload import (
    channel_config_reload_change_for_row,
    maybe_trigger_channel_config_reload,
)
from jiuwenclaw.gateway.channel_config_overlay import ChannelConfigChange

from ...infrastructure.utils import format_ts, utc_now
from ...models.application_config_models import CHANNEL_CONFIG_TABLE_DEF
from ...schemas.application_config_schemas import ChannelConfigCreateRequest
from ...infrastructure.db import ensure_db_handler

_TABLE = CHANNEL_CONFIG_TABLE_DEF.table_name
logger = logging.getLogger(__name__)


def _channel_row_to_dict(obj: Any) -> dict[str, Any]:
    raw_cfg = getattr(obj, "config", None)
    config = dict(raw_cfg) if isinstance(raw_cfg, dict) else raw_cfg
    return {
        "id": getattr(obj, "id"),
        "channel_id": getattr(obj, "channel_id"),
        "channel_name": getattr(obj, "channel_name"),
        "channel_type": getattr(obj, "channel_type"),
        "bot_id": getattr(obj, "bot_id"),
        "config": config,
        "status": str(getattr(obj, "status")),
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
    }


async def _create_channel_config_record(
    handler: DBHandler,
    request: ChannelConfigCreateRequest,
) -> dict[str, Any]:
    dup = await handler.get(_TABLE, {"channel_id": request.channel_id})
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
    record = await handler.create(_TABLE, row_data)
    return _channel_row_to_dict(record)


async def _get_channel_config_record(
    handler: DBHandler,
    channel_id: str,
) -> dict[str, Any] | None:
    row = await handler.get(_TABLE, {"channel_id": channel_id})
    if row is None:
        return None
    return _channel_row_to_dict(row)


async def _set_channel_status(
    handler: DBHandler,
    channel_id: str,
    target_status: str,
) -> dict[str, Any] | None:
    row = await handler.get(_TABLE, {"channel_id": channel_id})
    if row is None:
        return None
    now = utc_now()
    updated = await handler.update(
        _TABLE,
        {"channel_id": channel_id},
        {"status": target_status, "updated_at": now},
    )
    if updated is None:
        return None
    return _channel_row_to_dict(updated)


async def _delete_channel_config_record(
    handler: DBHandler,
    channel_id: str,
) -> bool:
    return await handler.delete(_TABLE, {"channel_id": channel_id})


async def list_active_channel_config_rows(handler: DBHandler) -> list[dict[str, Any]]:
    """冷启动全量：返回 ``status=active`` 的 ``channel_config`` 行（与 WS 写库行格式一致）。"""
    rows = await handler.list_records(_TABLE, {})
    result: list[dict[str, Any]] = []
    for row in rows:
        record = _channel_row_to_dict(row)
        if str(record.get("status") or "").strip().lower() == "active":
            result.append(record)
    return result


async def apply_channel_config(payload: dict[str, Any]) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 channel_config 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("channel_config.op is required")

    handler = await ensure_db_handler()

    if op == "create":
        channel = payload.get("channel")
        if not isinstance(channel, dict):
            raise ValueError("channel_config.create requires channel object")
        req = ChannelConfigCreateRequest.model_validate(channel)
        row = await _create_channel_config_record(handler, req)
        await maybe_trigger_channel_config_reload(
            channel_config_reload_change_for_row(row)
        )
        result: dict[str, Any] | None = {"channel_id": row["channel_id"]}

    elif op == "activate":
        channel_id = str(payload.get("channel_id") or "").strip()
        if not channel_id:
            raise ValueError("channel_config.activate requires channel_id")
        row = await _set_channel_status(handler, channel_id, "active")
        if row is None:
            raise ValueError(f"channel id={channel_id!r} not found")
        await maybe_trigger_channel_config_reload(ChannelConfigChange.upsert(row))
        result = {"channel_id": row["channel_id"], "status": row["status"]}

    elif op == "deactivate":
        channel_id = str(payload.get("channel_id") or "").strip()
        if not channel_id:
            raise ValueError("channel_config.deactivate requires channel_id")
        row = await _set_channel_status(handler, channel_id, "inactive")
        if row is None:
            raise ValueError(f"channel id={channel_id!r} not found")
        await maybe_trigger_channel_config_reload(ChannelConfigChange.remove(row))
        result = {"channel_id": row["channel_id"], "status": row["status"]}

    elif op == "delete":
        channel_id = str(payload.get("channel_id") or "").strip()
        if not channel_id:
            raise ValueError("channel_config.delete requires channel_id")
        row = await _get_channel_config_record(handler, channel_id)
        deleted = await _delete_channel_config_record(handler, channel_id)
        if not deleted:
            raise ValueError(f"channel id={channel_id!r} not found")
        if row is not None:
            await maybe_trigger_channel_config_reload(ChannelConfigChange.remove(row))
        result = None

    else:
        raise ValueError(f"unsupported channel_config.op: {op!r}")

    logger.info(
        "[ManagerWsClient] channel_config sync op=%s channel_id=%s",
        op,
        (result or {}).get("channel_id") or payload.get("channel_id"),
    )
    return result
