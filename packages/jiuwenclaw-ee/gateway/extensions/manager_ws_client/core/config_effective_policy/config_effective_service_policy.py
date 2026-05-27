# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Service 层级配置生效策略 WebSocket 同步：将 Claw Manager 下发的 config_effective_service_policies 写入 Gateway 本地库。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import assert_jiuwenclaw_id_matches_payload, get_jiuwenclaw_id, utc_now
from ...models.config_effective_policy_models import (
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from ...schemas.config_effective_policy_schemas import (
    ConfigEffectiveServicePolicyUpdateRequest,
)
from ...infrastructure.utils import (
    apply_template_ref_to_updates,
    normalize_template_ref,
)

_TABLE = CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name


async def _get_row_for_instance(
    handler: DBHandler,
    policy_id: int,
    jiuwenclaw_id: str,
) -> Any | None:
    row = await handler.get(_TABLE, {"id": policy_id})
    if row is None:
        return None
    if getattr(row, "jiuwenclaw_id", None) != jiuwenclaw_id:
        return None
    return row


async def update_config_effective_service_policy_record(
    handler: DBHandler,
    policy_id: int,
    request: ConfigEffectiveServicePolicyUpdateRequest,
) -> dict[str, Any] | None:
    jiuwenclaw_id = get_jiuwenclaw_id()
    existing = await _get_row_for_instance(handler, policy_id, jiuwenclaw_id)
    if existing is None:
        return None

    updates = request.model_dump(exclude_unset=True)
    if "service_id" in updates and updates["service_id"] is not None:
        updates["service_id"] = updates["service_id"].strip()
        if not updates["service_id"]:
            raise ValueError("service_id cannot be empty")

    if not updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    updates = apply_template_ref_to_updates(updates, existing_row=existing)
    updates["updated_at"] = utc_now()
    updated = await handler.update(_TABLE, {"id": policy_id}, updates)
    if updated is None:
        return None
    return {"id": getattr(updated, "id")}


async def delete_config_effective_service_policy_record(
    handler: DBHandler,
    policy_id: int,
) -> bool:
    jiuwenclaw_id = get_jiuwenclaw_id()
    existing = await _get_row_for_instance(handler, policy_id, jiuwenclaw_id)
    if existing is None:
        return False
    return await handler.delete(_TABLE, {"id": policy_id})


def _parse_iso_datetime(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    return value


async def apply_config_effective_service_policy_sync(
    handler: DBHandler,
    op: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 config_effective_service_policies 变更。"""
    jiuwenclaw_id = assert_jiuwenclaw_id_matches_payload(payload)

    if op == "create":
        policy = payload.get("policy")
        if not isinstance(policy, dict):
            raise ValueError(
                "config_effective_service_policies.create requires policy object"
            )
        service_id = str(policy["service_id"]).strip()
        if not service_id:
            raise ValueError("service_id is required")

        now = utc_now()
        row_data: dict[str, Any] = {
            "service_id": service_id,
            "jiuwenclaw_id": jiuwenclaw_id,
            "priority": int(policy["priority"]),
            "match_expr": policy.get("match_expr"),
            "template_ref": normalize_template_ref(policy.get("template_ref")),
            "enabled": bool(policy.get("enabled", True)),
            "data": policy.get("data"),
            "created_at": _parse_iso_datetime(policy.get("created_at")) or now,
            "updated_at": _parse_iso_datetime(policy.get("updated_at")) or now,
        }
        created = await handler.create(_TABLE, row_data)
        new_id = int(getattr(created, "id", 0) or 0)
        if new_id < 1:
            raise ValueError(
                "config_effective_service_policies.create: database did not return policy id"
            )
        return {"policy_id": new_id}

    if op == "update":
        policy_id = payload.get("policy_id")
        updates = payload.get("updates")
        if policy_id is None:
            raise ValueError(
                "config_effective_service_policies.update requires policy_id"
            )
        if not isinstance(updates, dict) or not updates:
            raise ValueError(
                "config_effective_service_policies.update requires non-empty updates"
            )
        req = ConfigEffectiveServicePolicyUpdateRequest.model_validate(updates)
        row = await update_config_effective_service_policy_record(
            handler, int(policy_id), req
        )
        if row is None:
            raise ValueError(f"config effective service policy id={policy_id} not found")
        return None

    if op == "delete":
        policy_id = payload.get("policy_id")
        if policy_id is None:
            raise ValueError(
                "config_effective_service_policies.delete requires policy_id"
            )
        deleted = await delete_config_effective_service_policy_record(
            handler, int(policy_id)
        )
        if not deleted:
            raise ValueError(f"config effective service policy id={policy_id} not found")
        return None

    raise ValueError(f"unsupported config_effective_service_policies.op: {op!r}")
