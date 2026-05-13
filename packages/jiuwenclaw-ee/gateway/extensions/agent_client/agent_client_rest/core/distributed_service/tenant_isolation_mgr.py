# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""租户隔离策略（tenant_isolation_policy）：基于 ``DBHandler`` 异步读写。

表结构见 ``TENANT_ISOLATION_POLICY_TABLE_DEF``。
"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ..utils import utc_now
from ...models.distributed_service_models import (
    TENANT_ISOLATION_POLICY_TABLE_DEF,
    TenantIsolationPolicyInfo,
)
from ...schemas.distributed_service_schemas import (
    TenantIsolationPolicyUpdateRequest,
)


def _orm_row_to_record(obj: Any) -> dict[str, Any]:
    payload = {
        "id": getattr(obj, "id"),
        "policy_name": getattr(obj, "policy_name"),
        "isolation_level": getattr(obj, "isolation_level"),
        "selector": getattr(obj, "selector"),
        "target_instances": getattr(obj, "target_instances"),
        "resource_quota": getattr(obj, "resource_quota"),
        "priority": getattr(obj, "priority"),
        "enabled": getattr(obj, "enabled"),
        "data": getattr(obj, "data"),
        "created_at": getattr(obj, "created_at"),
        "updated_at": getattr(obj, "updated_at"),
    }
    return TenantIsolationPolicyInfo(**payload).model_dump(mode="json")


async def list_tenant_isolation_policies(
    handler: DBHandler,
    *,
    isolation_level: str | None = None,
    enabled: bool | None = None,
    page_num: int = 1,
    page_size: int = 10,
) -> list[dict[str, Any]]:
    """列出策略；可按 ``isolation_level``、``enabled`` 过滤（AND）。

    ``page_num`` 从 1 起算；``limit = page_size``，``offset = (page_num - 1) * page_size``。
    默认 ``page_num=1``、``page_size=10``（即 ``limit=10``、``offset=0``）。
    """
    table = TENANT_ISOLATION_POLICY_TABLE_DEF.table_name
    filters: dict[str, Any] | None = None
    if isolation_level is not None or enabled is not None:
        filters = {}
        if isolation_level is not None:
            filters["isolation_level"] = isolation_level
        if enabled is not None:
            filters["enabled"] = enabled
    pn = max(1, page_num)
    ps = max(1, page_size)
    offset = (pn - 1) * ps
    rows = await handler.list_records(
        table,
        filters,
        limit=ps,
        offset=offset,
    )
    return [_orm_row_to_record(r) for r in rows]


async def upsert_tenant_isolation_policy(
    handler: DBHandler,
    *,
    policy_id: int,
    request: TenantIsolationPolicyUpdateRequest,
) -> dict[str, Any]:
    """插入或更新 ``tenant_isolation_policy``（按 ``id``）。

    无记录时插入一行（``id`` 为 ``policy_id``）；新建时 ``policy_name``、``isolation_level`` 必填。
    有记录时仅更新请求体中非 ``None`` 的字段；若无非空字段则不落库，直接返回当前行。
    返回与 GET 列表一致的 API 字典。
    """
    table = TENANT_ISOLATION_POLICY_TABLE_DEF.table_name
    existing = await handler.get(table, {"id": policy_id})
    now = utc_now()

    if existing is None:
        if request.policy_name is None or request.isolation_level is None:
            raise ValueError(
                "新建租户隔离策略时 policy_name 与 isolation_level 不能为空"
            )
        row_data: dict[str, Any] = {
            "id": policy_id,
            "policy_name": request.policy_name,
            "isolation_level": request.isolation_level,
            "selector": request.selector if request.selector is not None else {},
            "target_instances": (
                request.target_instances if request.target_instances is not None else []
            ),
            "resource_quota": request.resource_quota,
            "priority": request.priority if request.priority is not None else 0,
            "enabled": request.enabled if request.enabled is not None else True,
            "data": None,
            "created_at": now,
            "updated_at": now,
        }
        record = await handler.create(table, row_data)
        return _orm_row_to_record(record)

    patch: dict[str, Any] = {}
    for field in (
        "policy_name",
        "isolation_level",
        "selector",
        "target_instances",
        "resource_quota",
        "priority",
        "enabled",
    ):
        value = getattr(request, field)
        if value is not None:
            patch[field] = value

    if not patch:
        return _orm_row_to_record(existing)

    patch["updated_at"] = now
    updated = await handler.update(table, {"id": policy_id}, patch)
    if updated is None:
        raise RuntimeError("tenant isolation policy update returned no row")
    return _orm_row_to_record(updated)
