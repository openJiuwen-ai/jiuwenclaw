# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Service 层级配置生效策略 WebSocket 同步：将 Claw Manager 下发的 config_effective_service_policies 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.db import ensure_db_handler
from ...infrastructure.utils import (
    apply_template_ref_to_updates,
    get_jiuwenclaw_id,
    normalize_template_ref,
    parse_iso_datetime,
    utc_now,
)
from ...models.config_effective_policy_models import (
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from ...schemas.config_effective_policy_schemas import (
    ConfigEffectiveServicePolicyCreateRequest,
    ConfigEffectiveServicePolicyUpdateRequest,
)

_TABLE = CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name
logger = logging.getLogger(__name__)


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


async def apply_config_effective_service_policy(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 config_effective_service_policies 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("config_effective_service_policies.op is required")

    jiuwenclaw_id = get_jiuwenclaw_id()
    if not jiuwenclaw_id:
        raise ValueError("jiuwenclaw_id is not set")
    handler = await ensure_db_handler()

    if op == "create":
        policy = payload.get("policy")
        if not isinstance(policy, dict):
            raise ValueError(
                "config_effective_service_policies.create requires policy object"
            )
        req = ConfigEffectiveServicePolicyCreateRequest.model_validate(policy)
        now = utc_now()
        row_data: dict[str, Any] = {
            "jiuwenclaw_id": jiuwenclaw_id,
            "policy_id": req.policy_id,
            "policy_name": req.policy_name,
            "policy_desc": req.policy_desc,
            "service_id": req.service_id,
            "priority": req.priority,
            "match_expr": req.match_expr,
            "template_ref": normalize_template_ref(req.template_ref),
            "enabled": req.enabled,
            "data": req.data,
            "created_at": parse_iso_datetime(policy.get("created_at")) or now,
            "updated_at": parse_iso_datetime(policy.get("updated_at")) or now,
        }
        created = await handler.create(_TABLE, row_data)
        new_id = int(getattr(created, "id", 0) or 0)
        if new_id < 1:
            raise ValueError(
                "config_effective_service_policies.create: database did not return policy id"
            )
        result: dict[str, Any] | None = {"id": new_id}

    elif op == "update":
        row_id = payload.get("id")
        updates = payload.get("updates")
        if row_id is None:
            raise ValueError(
                "config_effective_service_policies.update requires id"
            )
        if not isinstance(updates, dict) or not updates:
            raise ValueError(
                "config_effective_service_policies.update requires non-empty updates"
            )
        req = ConfigEffectiveServicePolicyUpdateRequest.model_validate(updates)
        row = await update_config_effective_service_policy_record(
            handler, int(row_id), req
        )
        if row is None:
            raise ValueError(f"config effective service policy id={row_id} not found")
        result = None

    elif op == "delete":
        row_id = payload.get("id")
        if row_id is None:
            raise ValueError(
                "config_effective_service_policies.delete requires id"
            )
        deleted = await delete_config_effective_service_policy_record(
            handler, int(row_id)
        )
        if not deleted:
            raise ValueError(f"config effective service policy id={row_id} not found")
        result = None

    else:
        raise ValueError(f"unsupported config_effective_service_policies.op: {op!r}")

    logger.info(
        "[ManagerWsClient] config_effective_service_policies sync op=%s id=%s",
        op,
        (result or {}).get("id")
        or payload.get("id")
        or (payload.get("policy") or {}).get("id"),
    )
    return result
