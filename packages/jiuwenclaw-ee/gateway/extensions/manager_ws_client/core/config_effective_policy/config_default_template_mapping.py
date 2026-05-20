# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""默认模板映射 WebSocket 同步：将 Claw Manager 下发的 config_default_template_mappings 写入 Gateway 本地库。"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import utc_now
from ...models.config_effective_policy_models import (
    CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF,
)
from ...schemas.config_effective_policy_schemas import (
    ConfigDefaultTemplateMappingUpdateRequest,
)

_TABLE = CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF.table_name

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


async def _get_row_for_instance(
    handler: DBHandler,
    mapping_id: int,
    jiuwenclaw_id: str,
) -> Any | None:
    row = await handler.get(_TABLE, {"id": mapping_id})
    if row is None:
        return None
    if getattr(row, "jiuwenclaw_id", None) != jiuwenclaw_id:
        return None
    return row


async def update_config_default_template_mapping_record(
    handler: DBHandler,
    mapping_id: int,
    request: ConfigDefaultTemplateMappingUpdateRequest,
) -> dict[str, Any] | None:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    existing = await _get_row_for_instance(handler, mapping_id, jiuwenclaw_id)
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

    updates["updated_at"] = utc_now()
    updated = await handler.update(_TABLE, {"id": mapping_id}, updates)
    if updated is None:
        return None
    return {"id": getattr(updated, "id")}


async def delete_config_default_template_mapping_record(
    handler: DBHandler,
    mapping_id: int,
) -> bool:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    existing = await _get_row_for_instance(handler, mapping_id, jiuwenclaw_id)
    if existing is None:
        return False
    return await handler.delete(_TABLE, {"id": mapping_id})


def _parse_iso_datetime(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    return value


async def apply_config_default_template_mapping_sync(
    handler: DBHandler,
    op: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 config_default_template_mappings 变更。"""
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    payload_jid = str(payload.get("jiuwenclaw_id") or "").strip()
    if payload_jid and payload_jid != jiuwenclaw_id:
        raise ValueError(
            f"jiuwenclaw_id mismatch: push={payload_jid!r} env={jiuwenclaw_id!r}"
        )

    if op == "create":
        mapping = payload.get("mapping")
        if not isinstance(mapping, dict):
            raise ValueError(
                "config_default_template_mappings.create requires mapping object"
            )
        user_id, group_id = _validate_dimension_keys(
            mapping.get("user_id"), mapping.get("group_id")
        )
        template_type = _validate_template_type(str(mapping["template_type"]))
        template_id = str(mapping["template_id"]).strip()
        if not template_id:
            raise ValueError("template_id is required")

        now = utc_now()
        row_data: dict[str, Any] = {
            "jiuwenclaw_id": jiuwenclaw_id,
            "user_id": user_id,
            "group_id": group_id,
            "template_id": template_id,
            "template_type": template_type,
            "enabled": bool(mapping.get("enabled", True)),
            "data": mapping.get("data"),
            "created_at": _parse_iso_datetime(mapping.get("created_at")) or now,
            "updated_at": _parse_iso_datetime(mapping.get("updated_at")) or now,
        }
        created = await handler.create(_TABLE, row_data)
        new_id = int(getattr(created, "id", 0) or 0)
        if new_id < 1:
            raise ValueError(
                "config_default_template_mappings.create: database did not return mapping id"
            )
        return {"mapping_id": new_id}

    if op == "update":
        mapping_id = payload.get("mapping_id")
        updates = payload.get("updates")
        if mapping_id is None:
            raise ValueError(
                "config_default_template_mappings.update requires mapping_id"
            )
        if not isinstance(updates, dict) or not updates:
            raise ValueError(
                "config_default_template_mappings.update requires non-empty updates"
            )
        req = ConfigDefaultTemplateMappingUpdateRequest.model_validate(updates)
        row = await update_config_default_template_mapping_record(
            handler, int(mapping_id), req
        )
        if row is None:
            raise ValueError(f"config default template mapping id={mapping_id} not found")
        return None

    if op == "delete":
        mapping_id = payload.get("mapping_id")
        if mapping_id is None:
            raise ValueError(
                "config_default_template_mappings.delete requires mapping_id"
            )
        deleted = await delete_config_default_template_mapping_record(
            handler, int(mapping_id)
        )
        if not deleted:
            raise ValueError(f"config default template mapping id={mapping_id} not found")
        return None

    raise ValueError(f"unsupported config_default_template_mappings.op: {op!r}")
