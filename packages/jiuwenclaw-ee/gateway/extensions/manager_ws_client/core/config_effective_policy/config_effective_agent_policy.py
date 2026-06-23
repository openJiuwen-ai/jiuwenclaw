# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Agent 层级配置生效策略 WebSocket 同步：将 Claw Manager 下发的 config_effective_agent_policies 写入 Gateway 本地库。"""

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
    CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from ...schemas.config_effective_policy_schemas import (
    ConfigEffectiveAgentPolicyCreateRequest,
    ConfigEffectiveAgentPolicyUpdateRequest,
)

_AGENT_TABLE = CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF.table_name
_SERVICE_TABLE = CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name
logger = logging.getLogger(__name__)


async def _validate_service_policy_ref(
    handler: DBHandler,
    *,
    jiuwenclaw_id: str,
    service_policy_id: str,
) -> None:
    normalized_id = str(service_policy_id).strip()
    if not normalized_id:
        raise ValueError("service_policy_id is required")
    rows = await handler.list_records(
        _SERVICE_TABLE,
        {"jiuwenclaw_id": jiuwenclaw_id, "policy_id": normalized_id},
        limit=1,
        offset=0,
    )
    if not rows:
        raise ValueError(f"unknown service_policy_id={normalized_id!r}")


async def _get_row_for_instance(
    handler: DBHandler,
    policy_id: int,
    jiuwenclaw_id: str,
) -> Any | None:
    row = await handler.get(_AGENT_TABLE, {"id": policy_id})
    if row is None:
        return None
    if getattr(row, "jiuwenclaw_id", None) != jiuwenclaw_id:
        return None
    return row


async def update_config_effective_agent_policy_record(
    handler: DBHandler,
    policy_id: int,
    request: ConfigEffectiveAgentPolicyUpdateRequest,
) -> dict[str, Any] | None:
    jiuwenclaw_id = get_jiuwenclaw_id()
    existing = await _get_row_for_instance(handler, policy_id, jiuwenclaw_id)
    if existing is None:
        return None

    updates = request.model_dump(exclude_unset=True)
    if "agent_id" in updates and updates["agent_id"] is not None:
        updates["agent_id"] = updates["agent_id"].strip()
        if not updates["agent_id"]:
            raise ValueError("agent_id cannot be empty")

    if "service_policy_id" in updates and updates["service_policy_id"] is not None:
        updates["service_policy_id"] = str(updates["service_policy_id"]).strip()
        if not updates["service_policy_id"]:
            raise ValueError("service_policy_id cannot be empty")

    next_service_policy_id = updates.get(
        "service_policy_id", getattr(existing, "service_policy_id")
    )
    if "service_policy_id" in updates:
        await _validate_service_policy_ref(
            handler,
            jiuwenclaw_id=jiuwenclaw_id,
            service_policy_id=next_service_policy_id,
        )

    if not updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    updates = apply_template_ref_to_updates(updates, existing_row=existing)
    updates["updated_at"] = utc_now()
    updated = await handler.update(_AGENT_TABLE, {"id": policy_id}, updates)
    if updated is None:
        return None
    return {"id": getattr(updated, "id")}


async def delete_config_effective_agent_policy_record(
    handler: DBHandler,
    policy_id: int,
) -> bool:
    jiuwenclaw_id = get_jiuwenclaw_id()
    existing = await _get_row_for_instance(handler, policy_id, jiuwenclaw_id)
    if existing is None:
        return False
    return await handler.delete(_AGENT_TABLE, {"id": policy_id})


async def apply_config_effective_agent_policy(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 config_effective_agent_policies 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("config_effective_agent_policies.op is required")

    jiuwenclaw_id = get_jiuwenclaw_id()
    if not jiuwenclaw_id:
        raise ValueError("jiuwenclaw_id is not set")
    handler = await ensure_db_handler()

    if op == "create":
        policy = payload.get("policy")
        if not isinstance(policy, dict):
            raise ValueError(
                "config_effective_agent_policies.create requires policy object"
            )
        req = ConfigEffectiveAgentPolicyCreateRequest.model_validate(policy)
        await _validate_service_policy_ref(
            handler,
            jiuwenclaw_id=jiuwenclaw_id,
            service_policy_id=req.service_policy_id,
        )

        now = utc_now()
        row_data: dict[str, Any] = {
            "jiuwenclaw_id": jiuwenclaw_id,
            "policy_id": req.policy_id,
            "policy_name": req.policy_name,
            "policy_desc": req.policy_desc,
            "agent_id": req.agent_id,
            "service_policy_id": req.service_policy_id,
            "priority": req.priority,
            "match_expr": req.match_expr,
            "template_ref": normalize_template_ref(req.template_ref),
            "send_file_allowed": req.send_file_allowed,
            "enabled": req.enabled,
            "data": req.data,
            "created_at": parse_iso_datetime(policy.get("created_at")) or now,
            "updated_at": parse_iso_datetime(policy.get("updated_at")) or now,
        }
        created = await handler.create(_AGENT_TABLE, row_data)
        new_id = int(getattr(created, "id", 0) or 0)
        if new_id < 1:
            raise ValueError(
                "config_effective_agent_policies.create: database did not return policy id"
            )
        result: dict[str, Any] | None = {"id": new_id}

    elif op == "update":
        row_id = payload.get("id")
        updates = payload.get("updates")
        if row_id is None:
            raise ValueError(
                "config_effective_agent_policies.update requires id"
            )
        if not isinstance(updates, dict) or not updates:
            raise ValueError(
                "config_effective_agent_policies.update requires non-empty updates"
            )
        req = ConfigEffectiveAgentPolicyUpdateRequest.model_validate(updates)
        row = await update_config_effective_agent_policy_record(
            handler, int(row_id), req
        )
        if row is None:
            raise ValueError(f"config effective agent policy id={row_id} not found")
        result = None

    elif op == "delete":
        row_id = payload.get("id")
        if row_id is None:
            raise ValueError(
                "config_effective_agent_policies.delete requires id"
            )
        deleted = await delete_config_effective_agent_policy_record(
            handler, int(row_id)
        )
        if not deleted:
            raise ValueError(f"config effective agent policy id={row_id} not found")
        result = None

    else:
        raise ValueError(f"unsupported config_effective_agent_policies.op: {op!r}")

    logger.info(
        "[ManagerWsClient] config_effective_agent_policies sync op=%s id=%s",
        op,
        (result or {}).get("id")
        or payload.get("id")
        or (payload.get("policy") or {}).get("id"),
    )
    return result
