# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""全局兜底配置生效策略（config_effective_global_policy）持久化：基于 ``DBHandler`` 异步读写。

应用启动时由 ``agent_client_rest.app`` 的 lifespan 完成 ``connect`` 与
``init_table(CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF)``。

每个组网实例（``jiuwenclaw_id``）至多一条兜底策略。
"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import format_ts, utc_now
from ...models.config_effective_policy_models import (
    CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF,
)
from ...schemas.config_effective_policy_schemas import (
    ConfigEffectiveGlobalPolicyCreateRequest,
    ConfigEffectiveGlobalPolicyUpdateRequest,
)
from .config_default_template_mapping import resolve_jiuwenclaw_id


def _normalize_channel_ids(value: list[str]) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("channel_ids must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def _global_policy_row_to_dict(obj: Any) -> dict[str, Any]:
    channel_ids = getattr(obj, "channel_ids", None)
    if not isinstance(channel_ids, list):
        channel_ids = list(channel_ids) if channel_ids else []
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "default_model": getattr(obj, "default_model"),
        "video_model": getattr(obj, "video_model"),
        "audio_model": getattr(obj, "audio_model"),
        "vision_model": getattr(obj, "vision_model"),
        "channel_ids": channel_ids,
        "enabled": getattr(obj, "enabled"),
        "data": getattr(obj, "data", None),
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
    }


async def _ensure_unique_jiuwenclaw_id(
    handler: DBHandler,
    jiuwenclaw_id: str,
    *,
    exclude_policy_id: int | None = None,
) -> None:
    rows = await handler.list_records(
        CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF.table_name,
        {"jiuwenclaw_id": jiuwenclaw_id},
        limit=2,
        offset=0,
    )
    for row in rows:
        row_id = getattr(row, "id", None)
        if exclude_policy_id is not None and row_id == exclude_policy_id:
            continue
        raise ValueError(
            f"global policy for jiuwenclaw_id={jiuwenclaw_id!r} already exists (id={row_id})"
        )


async def _get_global_policy_row_for_instance(
    handler: DBHandler,
    policy_id: int,
    jiuwenclaw_id: str,
) -> Any | None:
    row = await handler.get(
        CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF.table_name,
        {"id": policy_id},
    )
    if row is None:
        return None
    if getattr(row, "jiuwenclaw_id", None) != jiuwenclaw_id:
        return None
    return row


async def create_config_effective_global_policy_record(
    handler: DBHandler,
    request: ConfigEffectiveGlobalPolicyCreateRequest,
) -> dict[str, Any]:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    await _ensure_unique_jiuwenclaw_id(handler, jiuwenclaw_id)
    now = utc_now()
    row_data: dict[str, Any] = {
        "jiuwenclaw_id": jiuwenclaw_id,
        "default_model": request.default_model,
        "video_model": request.video_model,
        "audio_model": request.audio_model,
        "vision_model": request.vision_model,
        "channel_ids": _normalize_channel_ids(request.channel_ids),
        "enabled": request.enabled,
        "data": request.data,
        "created_at": now,
        "updated_at": now,
    }
    record = await handler.create(
        CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF.table_name,
        row_data,
    )
    return _global_policy_row_to_dict(record)


async def list_config_effective_global_policy_records(
    handler: DBHandler,
    *,
    enabled: bool | None = None,
    page_size: int = 20,
    page_num: int = 1,
) -> dict[str, Any]:
    """分页列出全局兜底策略（每实例通常 0～1 条）。"""
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    filters: dict[str, Any] = {"jiuwenclaw_id": jiuwenclaw_id}
    if enabled is not None:
        filters["enabled"] = enabled

    limit = min(max(page_size, 1), 200)
    page_num = max(page_num, 1)
    offset = (page_num - 1) * limit
    rows = await handler.list_records(
        CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF.table_name,
        filters,
        limit=limit,
        offset=offset,
    )
    total = await handler.count_records(
        CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF.table_name,
        filters,
    )
    items = [_global_policy_row_to_dict(r) for r in rows]
    return {"items": items, "total": total}


async def get_config_effective_global_policy_record(
    handler: DBHandler,
    policy_id: int,
) -> dict[str, Any] | None:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    row = await _get_global_policy_row_for_instance(handler, policy_id, jiuwenclaw_id)
    if row is None:
        return None
    return _global_policy_row_to_dict(row)


async def update_config_effective_global_policy_record(
    handler: DBHandler,
    policy_id: int,
    request: ConfigEffectiveGlobalPolicyUpdateRequest,
) -> dict[str, Any] | None:
    """按 ``policy_id`` 更新全局兜底策略；不存在或更新后读回失败时返回 ``None``。"""
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    existing = await _get_global_policy_row_for_instance(handler, policy_id, jiuwenclaw_id)
    if existing is None:
        return None

    updates = request.model_dump(exclude_unset=True)
    if "channel_ids" in updates and updates["channel_ids"] is not None:
        updates["channel_ids"] = _normalize_channel_ids(updates["channel_ids"])

    if not updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    now = utc_now()
    updated = await handler.update(
        CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF.table_name,
        {"id": policy_id},
        {**updates, "updated_at": now},
    )
    if updated is None:
        return None
    return _global_policy_row_to_dict(updated)


async def delete_config_effective_global_policy_record(
    handler: DBHandler,
    policy_id: int,
) -> bool:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    existing = await _get_global_policy_row_for_instance(handler, policy_id, jiuwenclaw_id)
    if existing is None:
        return False
    return await handler.delete(
        CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF.table_name,
        {"id": policy_id},
    )
