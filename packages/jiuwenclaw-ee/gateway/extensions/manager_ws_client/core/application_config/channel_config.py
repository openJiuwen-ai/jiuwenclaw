# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Channel 配置 WebSocket 同步：将 Claw Manager 下发的 channel_config 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from jiuwenclaw.gateway.channel_config_overlay import ChannelConfigChange
from jiuwenclaw.gateway.channel_config_reload import (
    channel_config_reload_change_for_row,
    maybe_trigger_channel_config_reload,
)

from jiuwenswarm.gateway.config.channel.models import ChannelConfig
from jiuwenswarm.gateway.config.channel.repository import ChannelConfigRepository

from ...infrastructure.repository_access import require_channel_repository
from ...infrastructure.utils import format_ts, get_jiuwenclaw_id, utc_now
from ...schemas.application_config_schemas import ChannelConfigCreateRequest

logger = logging.getLogger(__name__)


def _channel_config_to_row(config: ChannelConfig, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = dict(extra or {})
    return {
        "id": meta.get("id"),
        "jiuwenclaw_id": meta.get("jiuwenclaw_id") or get_jiuwenclaw_id(),
        "channel_id": config.channel_id,
        "channel_name": config.channel_name,
        "channel_type": config.channel_type,
        "bot_id": config.bot_id,
        "config": dict(config.body),
        "status": str(config.status),
        "created_at": format_ts(meta.get("created_at")),
        "updated_at": format_ts(meta.get("updated_at")),
    }


async def _create_channel_config_record(
    repo: ChannelConfigRepository,
    request: ChannelConfigCreateRequest,
) -> dict[str, Any]:
    existing = await repo.get(request.channel_id)
    if existing is not None:
        raise ValueError("channel_id already exists")
    now = utc_now()
    config = ChannelConfig(
        channel_id=request.channel_id,
        body=dict(request.config),
        channel_name=request.channel_name,
        channel_type=request.channel_type,
        bot_id=request.bot_id,
        status=request.status,
    )
    saved = await repo.create(config)
    return _channel_config_to_row(saved, extra={"created_at": now, "updated_at": now})


async def _get_channel_config_record(
    repo: ChannelConfigRepository,
    channel_id: str,
) -> dict[str, Any] | None:
    config = await repo.get(channel_id)
    if config is None:
        return None
    return _channel_config_to_row(config)


async def _set_channel_status(
    repo: ChannelConfigRepository,
    channel_id: str,
    target_status: str,
) -> dict[str, Any] | None:
    existing = await repo.get(channel_id)
    if existing is None:
        return None
    updated = ChannelConfig(
        channel_id=existing.channel_id,
        body=dict(existing.body),
        channel_name=existing.channel_name,
        channel_type=existing.channel_type,
        bot_id=existing.bot_id,
        status=target_status,
    )
    saved = await repo.update(updated)
    if saved is None:
        return None
    return _channel_config_to_row(saved, extra={"updated_at": utc_now()})


async def list_active_channel_config_rows(
    repo: ChannelConfigRepository | None = None,
) -> list[dict[str, Any]]:
    """冷启动全量：返回当前实例 ``status=active`` 的 ``channel_config`` 行。"""
    if not get_jiuwenclaw_id():
        return []
    store = repo or require_channel_repository()
    result: list[dict[str, Any]] = []
    for config in await store.list():
        if str(config.status or "").strip().lower() == "active":
            result.append(_channel_config_to_row(config))
    return result


async def apply_channel_config(payload: dict[str, Any]) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 channel_config 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("channel_config.op is required")

    if not get_jiuwenclaw_id():
        raise ValueError("jiuwenclaw_id is not set")

    repo = require_channel_repository()

    if op == "create":
        channel = payload.get("channel")
        if not isinstance(channel, dict):
            raise ValueError("channel_config.create requires channel object")
        req = ChannelConfigCreateRequest.model_validate(channel)
        row = await _create_channel_config_record(repo, req)
        await maybe_trigger_channel_config_reload(
            channel_config_reload_change_for_row(row)
        )
        result: dict[str, Any] | None = {"channel_id": row["channel_id"]}

    elif op == "activate":
        channel_id = str(payload.get("channel_id") or "").strip()
        if not channel_id:
            raise ValueError("channel_config.activate requires channel_id")
        row = await _set_channel_status(repo, channel_id, "active")
        if row is None:
            raise ValueError(f"channel id={channel_id!r} not found")
        await maybe_trigger_channel_config_reload(ChannelConfigChange.upsert(row))
        result = {"channel_id": row["channel_id"], "status": row["status"]}

    elif op == "deactivate":
        channel_id = str(payload.get("channel_id") or "").strip()
        if not channel_id:
            raise ValueError("channel_config.deactivate requires channel_id")
        row = await _set_channel_status(repo, channel_id, "inactive")
        if row is None:
            raise ValueError(f"channel id={channel_id!r} not found")
        await maybe_trigger_channel_config_reload(ChannelConfigChange.remove(row))
        result = {"channel_id": row["channel_id"], "status": row["status"]}

    elif op == "delete":
        channel_id = str(payload.get("channel_id") or "").strip()
        if not channel_id:
            raise ValueError("channel_config.delete requires channel_id")
        row = await _get_channel_config_record(repo, channel_id)
        deleted = await repo.delete(channel_id)
        if not deleted:
            raise ValueError(f"channel id={channel_id!r} not found")
        if row is not None:
            await maybe_trigger_channel_config_reload(ChannelConfigChange.remove(row))
        result = None

    else:
        raise ValueError(f"unsupported channel_config.op: {op!r}")

    logger.info(
        "[ManagerWsClient] channel_config sync op=%s jiuwenclaw_id=%s channel_id=%s",
        op,
        get_jiuwenclaw_id(),
        (result or {}).get("channel_id") or payload.get("channel_id"),
    )
    return result
