# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Agent 层级配置生效策略 WebSocket 同步：将 Claw Manager 下发的 config_effective_agent_policies 写入 Gateway 本地库。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import assert_jiuwenclaw_id_matches_payload, get_jiuwenclaw_id, utc_now
from ...models.config_effective_policy_models import (
    CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from ...schemas.config_effective_policy_schemas import (
    ConfigEffectiveAgentPolicyUpdateRequest,
)
from ...infrastructure.utils import (
    apply_template_ref_to_updates,
    normalize_template_ref,
)

_AGENT_TABLE = CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF.table_name
_SERVICE_TABLE = CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name


async def _validate_service_policy_ref(
    handler: DBHandler,
    *,
    jiuwenclaw_id: str,
    service_policy_id: int,
) -> None:
    row = await handler.get(_SERVICE_TABLE, {"id": service_policy_id})
    if row is None:
        raise ValueError(f"unknown service_policy_id={service_policy_id}")
    if getattr(row, "jiuwenclaw_id", None) != jiuwenclaw_id:
        raise ValueError(
            "service_policy_id does not belong to the current jiuwenclaw instance"
        )


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


def _parse_iso_datetime(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    return value


async def apply_config_effective_agent_policy_sync(
    handler: DBHandler,
    op: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 config_effective_agent_policies 变更。"""
    jiuwenclaw_id = assert_jiuwenclaw_id_matches_payload(payload)

    if op == "create":
        policy = payload.get("policy")
        if not isinstance(policy, dict):
            raise ValueError(
                "config_effective_agent_policies.create requires policy object"
            )
        agent_id = str(policy["agent_id"]).strip()
        if not agent_id:
            raise ValueError("agent_id is required")
        service_policy_id = int(policy["service_policy_id"])
        await _validate_service_policy_ref(
            handler,
            jiuwenclaw_id=jiuwenclaw_id,
            service_policy_id=service_policy_id,
        )

        now = utc_now()
        row_data: dict[str, Any] = {
            "agent_id": agent_id,
            "jiuwenclaw_id": jiuwenclaw_id,
            "service_policy_id": service_policy_id,
            "priority": int(policy.get("priority", 0)),
            "match_expr": policy.get("match_expr"),
            "template_ref": normalize_template_ref(policy.get("template_ref")),
            "enabled": bool(policy.get("enabled", True)),
            "data": policy.get("data"),
            "created_at": _parse_iso_datetime(policy.get("created_at")) or now,
            "updated_at": _parse_iso_datetime(policy.get("updated_at")) or now,
        }
        created = await handler.create(_AGENT_TABLE, row_data)
        new_id = int(getattr(created, "id", 0) or 0)
        if new_id < 1:
            raise ValueError(
                "config_effective_agent_policies.create: database did not return policy id"
            )
        return {"policy_id": new_id}

    if op == "update":
        policy_id = payload.get("policy_id")
        updates = payload.get("updates")
        if policy_id is None:
            raise ValueError(
                "config_effective_agent_policies.update requires policy_id"
            )
        if not isinstance(updates, dict) or not updates:
            raise ValueError(
                "config_effective_agent_policies.update requires non-empty updates"
            )
        req = ConfigEffectiveAgentPolicyUpdateRequest.model_validate(updates)
        row = await update_config_effective_agent_policy_record(
            handler, int(policy_id), req
        )
        if row is None:
            raise ValueError(f"config effective agent policy id={policy_id} not found")
        return None

    if op == "delete":
        policy_id = payload.get("policy_id")
        if policy_id is None:
            raise ValueError(
                "config_effective_agent_policies.delete requires policy_id"
            )
        deleted = await delete_config_effective_agent_policy_record(
            handler, int(policy_id)
        )
        if not deleted:
            raise ValueError(f"config effective agent policy id={policy_id} not found")
        return None

    raise ValueError(f"unsupported config_effective_agent_policies.op: {op!r}")
