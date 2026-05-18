# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Agent 层级配置生效策略（config_effective_agent_policy）持久化：基于 ``DBHandler`` 异步读写。

应用启动时由 ``agent_client_rest.app`` 的 lifespan 完成 ``connect`` 与
``init_table(CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF)``。
"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ..utils import format_ts, utc_now
from ...models.config_effective_policy_models import (
    CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from ...schemas.config_effective_policy_schemas import (
    ConfigEffectiveAgentPolicyCreateRequest,
    ConfigEffectiveAgentPolicyUpdateRequest,
)
from .config_default_template_mapping import resolve_jiuwenclaw_id


def _agent_policy_row_to_dict(obj: Any) -> dict[str, Any]:
    return {
        "id": getattr(obj, "id"),
        "agent_id": getattr(obj, "agent_id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "service_policy_id": getattr(obj, "service_policy_id"),
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


async def _validate_service_policy_ref(
    handler: DBHandler,
    *,
    jiuwenclaw_id: str,
    service_policy_id: int,
) -> None:
    row = await handler.get(
        CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name,
        {"id": service_policy_id},
    )
    if row is None:
        raise ValueError(f"unknown service_policy_id={service_policy_id}")
    if getattr(row, "jiuwenclaw_id", None) != jiuwenclaw_id:
        raise ValueError(
            "service_policy_id does not belong to the current jiuwenclaw instance"
        )


async def _get_agent_policy_row_for_instance(
    handler: DBHandler,
    policy_id: int,
    jiuwenclaw_id: str,
) -> Any | None:
    row = await handler.get(
        CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF.table_name,
        {"id": policy_id},
    )
    if row is None:
        return None
    if getattr(row, "jiuwenclaw_id", None) != jiuwenclaw_id:
        return None
    return row


async def create_config_effective_agent_policy_record(
    handler: DBHandler,
    request: ConfigEffectiveAgentPolicyCreateRequest,
) -> dict[str, Any]:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    await _validate_service_policy_ref(
        handler,
        jiuwenclaw_id=jiuwenclaw_id,
        service_policy_id=request.service_policy_id,
    )
    now = utc_now()
    row_data: dict[str, Any] = {
        "agent_id": request.agent_id,
        "jiuwenclaw_id": jiuwenclaw_id,
        "service_policy_id": request.service_policy_id,
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
        CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF.table_name,
        row_data,
    )
    return _agent_policy_row_to_dict(record)


async def list_config_effective_agent_policy_records(
    handler: DBHandler,
    *,
    service_policy_id: int | None = None,
    enabled: bool | None = None,
    page_size: int = 20,
    page_num: int = 1,
) -> dict[str, Any]:
    """分页列出 Agent 层级策略；``limit=page_size``，``offset=(page_num-1)*page_size``。"""
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    filters: dict[str, Any] = {"jiuwenclaw_id": jiuwenclaw_id}
    if service_policy_id is not None:
        filters["service_policy_id"] = service_policy_id
    if enabled is not None:
        filters["enabled"] = enabled

    limit = min(max(page_size, 1), 200)
    page_num = max(page_num, 1)
    offset = (page_num - 1) * limit
    rows = await handler.list_records(
        CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF.table_name,
        filters,
        limit=limit,
        offset=offset,
    )
    total = await handler.count_records(
        CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF.table_name,
        filters,
    )
    items = [_agent_policy_row_to_dict(r) for r in rows]
    return {"items": items, "total": total}


async def get_config_effective_agent_policy_record(
    handler: DBHandler,
    policy_id: int,
) -> dict[str, Any] | None:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    row = await _get_agent_policy_row_for_instance(handler, policy_id, jiuwenclaw_id)
    if row is None:
        return None
    return _agent_policy_row_to_dict(row)


async def update_config_effective_agent_policy_record(
    handler: DBHandler,
    policy_id: int,
    request: ConfigEffectiveAgentPolicyUpdateRequest,
) -> dict[str, Any] | None:
    """按 ``policy_id`` 更新 Agent 策略；不存在或更新后读回失败时返回 ``None``。"""
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    existing = await _get_agent_policy_row_for_instance(handler, policy_id, jiuwenclaw_id)
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

    now = utc_now()
    updated = await handler.update(
        CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF.table_name,
        {"id": policy_id},
        {**updates, "updated_at": now},
    )
    if updated is None:
        return None
    return _agent_policy_row_to_dict(updated)


async def delete_config_effective_agent_policy_record(
    handler: DBHandler,
    policy_id: int,
) -> bool:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    existing = await _get_agent_policy_row_for_instance(handler, policy_id, jiuwenclaw_id)
    if existing is None:
        return False
    return await handler.delete(
        CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF.table_name,
        {"id": policy_id},
    )
