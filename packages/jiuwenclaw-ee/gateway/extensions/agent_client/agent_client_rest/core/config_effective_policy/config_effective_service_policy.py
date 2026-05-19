# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Service 层级配置生效策略（config_effective_service_policy）持久化：基于 ``DBHandler`` 异步读写。

应用启动时由 ``agent_client_rest.app`` 的 lifespan 完成 ``connect`` 与
``init_table(CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF)``。
"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import format_ts, utc_now
from ...models.config_effective_policy_models import (
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from ...schemas.config_effective_policy_schemas import (
    ConfigEffectiveServicePolicyCreateRequest,
    ConfigEffectiveServicePolicyUpdateRequest,
)
from .config_default_template_mapping import resolve_jiuwenclaw_id


def _service_policy_row_to_dict(obj: Any) -> dict[str, Any]:
    return {
        "id": getattr(obj, "id"),
        "service_id": getattr(obj, "service_id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "priority": getattr(obj, "priority"),
        "match_expr": getattr(obj, "match_expr"),
        "default_model": getattr(obj, "default_model"),
        "video_model": getattr(obj, "video_model"),
        "audio_model": getattr(obj, "audio_model"),
        "vision_model": getattr(obj, "vision_model"),
        "enabled": getattr(obj, "enabled"),
        "data": getattr(obj, "data", None),
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
    }


async def _get_service_policy_row_for_instance(
    handler: DBHandler,
    policy_id: int,
    jiuwenclaw_id: str,
) -> Any | None:
    row = await handler.get(
        CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name,
        {"id": policy_id},
    )
    if row is None:
        return None
    if getattr(row, "jiuwenclaw_id", None) != jiuwenclaw_id:
        return None
    return row


async def create_config_effective_service_policy_record(
    handler: DBHandler,
    request: ConfigEffectiveServicePolicyCreateRequest,
) -> dict[str, Any]:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    now = utc_now()
    row_data: dict[str, Any] = {
        "service_id": request.service_id,
        "jiuwenclaw_id": jiuwenclaw_id,
        "priority": request.priority,
        "match_expr": request.match_expr,
        "default_model": request.default_model,
        "video_model": request.video_model,
        "audio_model": request.audio_model,
        "vision_model": request.vision_model,
        "enabled": request.enabled,
        "data": request.data,
        "created_at": now,
        "updated_at": now,
    }
    record = await handler.create(
        CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name,
        row_data,
    )
    return _service_policy_row_to_dict(record)


async def list_config_effective_service_policy_records(
    handler: DBHandler,
    *,
    enabled: bool | None = None,
    page_size: int = 20,
    page_num: int = 1,
) -> dict[str, Any]:
    """分页列出 Service 层级策略；``limit=page_size``，``offset=(page_num-1)*page_size``。"""
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    filters: dict[str, Any] = {"jiuwenclaw_id": jiuwenclaw_id}
    if enabled is not None:
        filters["enabled"] = enabled

    limit = min(max(page_size, 1), 200)
    page_num = max(page_num, 1)
    offset = (page_num - 1) * limit
    rows = await handler.list_records(
        CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name,
        filters,
        limit=limit,
        offset=offset,
    )
    total = await handler.count_records(
        CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name,
        filters,
    )
    items = [_service_policy_row_to_dict(r) for r in rows]
    return {"items": items, "total": total}


async def get_config_effective_service_policy_record(
    handler: DBHandler,
    policy_id: int,
) -> dict[str, Any] | None:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    row = await _get_service_policy_row_for_instance(handler, policy_id, jiuwenclaw_id)
    if row is None:
        return None
    return _service_policy_row_to_dict(row)


async def update_config_effective_service_policy_record(
    handler: DBHandler,
    policy_id: int,
    request: ConfigEffectiveServicePolicyUpdateRequest,
) -> dict[str, Any] | None:
    """按 ``policy_id`` 更新 Service 策略；不存在或更新后读回失败时返回 ``None``。"""
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    existing = await _get_service_policy_row_for_instance(handler, policy_id, jiuwenclaw_id)
    if existing is None:
        return None

    updates = request.model_dump(exclude_unset=True)
    if "service_id" in updates and updates["service_id"] is not None:
        updates["service_id"] = updates["service_id"].strip()
        if not updates["service_id"]:
            raise ValueError("service_id cannot be empty")

    if not updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    now = utc_now()
    updated = await handler.update(
        CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name,
        {"id": policy_id},
        {**updates, "updated_at": now},
    )
    if updated is None:
        return None
    return _service_policy_row_to_dict(updated)


async def delete_config_effective_service_policy_record(
    handler: DBHandler,
    policy_id: int,
) -> bool:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    existing = await _get_service_policy_row_for_instance(handler, policy_id, jiuwenclaw_id)
    if existing is None:
        return False
    return await handler.delete(
        CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name,
        {"id": policy_id},
    )
