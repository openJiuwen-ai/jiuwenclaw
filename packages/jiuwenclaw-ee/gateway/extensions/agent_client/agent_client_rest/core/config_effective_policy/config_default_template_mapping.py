# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""用户与群组默认模板映射（config_default_template_mapping）持久化：基于 ``DBHandler`` 异步读写。

应用启动时由 ``agent_client_rest.app`` 的 lifespan 完成 ``connect`` 与
``init_table(CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF)``。
"""

from __future__ import annotations

import os
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import format_ts, utc_now
from ...models.config_effective_policy_models import (
    CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF,
)
from ...schemas.config_effective_policy_schemas import (
    ConfigDefaultTemplateMappingCreateRequest,
    ConfigDefaultTemplateMappingUpdateRequest,
)

_ALLOWED_TEMPLATE_TYPES = frozenset({
    "model",
    "channel",
    "skill_whitelist",
    "service_resource",
})


def resolve_jiuwenclaw_id() -> str:
    instance_id = os.getenv("JIUWENCLAW_PROVISIONED_INSTANCE_ID", "").strip()
    if not instance_id:
        raise ValueError("JIUWENCLAW_PROVISIONED_INSTANCE_ID is not set")
    return instance_id


def _optional_key(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _validate_template_type(template_type: str) -> str:
    normalized = template_type.strip()
    if normalized not in _ALLOWED_TEMPLATE_TYPES:
        raise ValueError(
            f"template_type must be one of {sorted(_ALLOWED_TEMPLATE_TYPES)}, got {template_type!r}"
        )
    return normalized


def _validate_dimension_keys(
    user_id: str | None, group_id: str | None
) -> tuple[str | None, str | None]:
    uid = _optional_key(user_id)
    gid = _optional_key(group_id)
    if uid is None and gid is None:
        raise ValueError("at least one of user_id or group_id is required")
    return uid, gid


def _mapping_row_to_dict(obj: Any) -> dict[str, Any]:
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "user_id": getattr(obj, "user_id"),
        "group_id": getattr(obj, "group_id"),
        "template_id": getattr(obj, "template_id"),
        "template_type": getattr(obj, "template_type"),
        "enabled": getattr(obj, "enabled"),
        "data": getattr(obj, "data", None),
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
    }


async def _get_mapping_row_for_instance(
    handler: DBHandler,
    mapping_id: int,
    jiuwenclaw_id: str,
) -> Any | None:
    row = await handler.get(
        CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF.table_name,
        {"id": mapping_id},
    )
    if row is None:
        return None
    if getattr(row, "jiuwenclaw_id", None) != jiuwenclaw_id:
        return None
    return row


async def create_config_default_template_mapping_record(
    handler: DBHandler,
    request: ConfigDefaultTemplateMappingCreateRequest,
) -> dict[str, Any]:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    user_id, group_id = _validate_dimension_keys(request.user_id, request.group_id)
    template_type = _validate_template_type(request.template_type)
    template_id = request.template_id.strip()
    if not template_id:
        raise ValueError("template_id is required")

    now = utc_now()
    row_data: dict[str, Any] = {
        "jiuwenclaw_id": jiuwenclaw_id,
        "user_id": user_id,
        "group_id": group_id,
        "template_id": template_id,
        "template_type": template_type,
        "enabled": request.enabled,
        "data": request.data,
        "created_at": now,
        "updated_at": now,
    }
    record = await handler.create(
        CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF.table_name,
        row_data,
    )
    return _mapping_row_to_dict(record)


async def list_config_default_template_mapping_records(
    handler: DBHandler,
    *,
    user_id: str | None = None,
    group_id: str | None = None,
    template_type: str | None = None,
    template_id: str | None = None,
    enabled: bool | None = None,
    page_size: int = 20,
    page_num: int = 1,
) -> dict[str, Any]:
    """分页列出默认模板映射；``limit=page_size``，``offset=(page_num-1)*page_size``。"""
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    filters: dict[str, Any] = {"jiuwenclaw_id": jiuwenclaw_id}
    if user_id:
        filters["user_id"] = _optional_key(user_id)
    if group_id:
        filters["group_id"] = _optional_key(group_id)
    if template_type:
        filters["template_type"] = _validate_template_type(template_type)
    if template_id:
        filters["template_id"] = template_id.strip()
    if enabled is not None:
        filters["enabled"] = enabled

    limit = min(max(page_size, 1), 200)
    page_num = max(page_num, 1)
    offset = (page_num - 1) * limit
    rows = await handler.list_records(
        CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF.table_name,
        filters,
        limit=limit,
        offset=offset,
    )
    total = await handler.count_records(
        CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF.table_name,
        filters,
    )
    items = [_mapping_row_to_dict(r) for r in rows]
    return {"items": items, "total": total}


async def get_config_default_template_mapping_record(
    handler: DBHandler,
    mapping_id: int,
) -> dict[str, Any] | None:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    row = await _get_mapping_row_for_instance(handler, mapping_id, jiuwenclaw_id)
    if row is None:
        return None
    return _mapping_row_to_dict(row)


async def update_config_default_template_mapping_record(
    handler: DBHandler,
    mapping_id: int,
    request: ConfigDefaultTemplateMappingUpdateRequest,
) -> dict[str, Any] | None:
    """按 ``mapping_id`` 更新映射；不存在或更新后读回失败时返回 ``None``。"""
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    existing = await _get_mapping_row_for_instance(handler, mapping_id, jiuwenclaw_id)
    if existing is None:
        return None

    updates = request.model_dump(exclude_unset=True)
    if "template_type" in updates and updates["template_type"] is not None:
        updates["template_type"] = _validate_template_type(updates["template_type"])
    if "template_id" in updates and updates["template_id"] is not None:
        updates["template_id"] = updates["template_id"].strip()
        if not updates["template_id"]:
            raise ValueError("template_id cannot be empty")
    if "user_id" in updates:
        updates["user_id"] = _optional_key(updates["user_id"])
    if "group_id" in updates:
        updates["group_id"] = _optional_key(updates["group_id"])

    merged_user = updates.get("user_id", getattr(existing, "user_id", None))
    merged_group = updates.get("group_id", getattr(existing, "group_id", None))
    _validate_dimension_keys(merged_user, merged_group)

    if not updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    now = utc_now()
    updated = await handler.update(
        CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF.table_name,
        {"id": mapping_id},
        {**updates, "updated_at": now},
    )
    if updated is None:
        return None
    return _mapping_row_to_dict(updated)


async def delete_config_default_template_mapping_record(
    handler: DBHandler,
    mapping_id: int,
) -> bool:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    existing = await _get_mapping_row_for_instance(handler, mapping_id, jiuwenclaw_id)
    if existing is None:
        return False
    return await handler.delete(
        CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF.table_name,
        {"id": mapping_id},
    )
