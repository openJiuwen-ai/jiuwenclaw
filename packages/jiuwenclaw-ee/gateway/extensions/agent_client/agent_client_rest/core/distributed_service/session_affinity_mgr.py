# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Session 亲和策略（session_affinity_policy）：基于 ``DBHandler`` 异步读写。

GET 返回分页列表；PUT 以请求体中的 ``policy_name`` 为业务唯一键查找行，更新或插入。
"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import utc_now
from ...models.distributed_service_models import (
    SESSION_AFFINITY_POLICY_TABLE_DEF,
    SessionAffinityPolicyInfo,
)
from ...schemas.distributed_service_schemas import (
    SessionAffinityPolicyUpdateRequest,
)

TABLE = SESSION_AFFINITY_POLICY_TABLE_DEF.table_name

_UPDATE_FIELDS = (
    "policy_name",
    "affinity_type",
    "session_ttl",
    "max_concurrent_per_session",
    "failover_enabled",
)


def _orm_row_to_record(obj: Any) -> dict[str, Any]:
    payload = {
        "id": getattr(obj, "id"),
        "policy_name": getattr(obj, "policy_name"),
        "affinity_type": getattr(obj, "affinity_type"),
        "session_ttl": getattr(obj, "session_ttl"),
        "max_concurrent_per_session": getattr(obj, "max_concurrent_per_session", None),
        "failover_enabled": getattr(obj, "failover_enabled"),
        "data": getattr(obj, "data", None),
        "created_at": getattr(obj, "created_at"),
        "updated_at": getattr(obj, "updated_at"),
    }
    return SessionAffinityPolicyInfo(**payload).model_dump(mode="json")


def _non_none_request_fields(request: SessionAffinityPolicyUpdateRequest) -> dict[str, Any]:
    """请求体中显式给出的非空字段（用于插入或部分更新）。"""
    out: dict[str, Any] = {}
    for field in _UPDATE_FIELDS:
        value = getattr(request, field)
        if value is None:
            continue
        if field == "session_ttl":
            out[field] = int(value)
        elif field == "failover_enabled":
            out[field] = bool(value)
        else:
            out[field] = value
    return out


async def list_session_affinity_policies(
    handler: DBHandler,
    *,
    page_num: int = 1,
    page_size: int = 10,
) -> dict[str, Any]:
    """列出 session 亲和策略；``page_num`` 从 1 起算，``limit`` / ``offset`` 与 ``list_session_mappings`` 一致。

    默认 ``page_num=1``、``page_size=10``（即 ``limit=10``、``offset=0``）。
    """
    pn = max(1, page_num)
    ps = max(1, page_size)
    offset = (pn - 1) * ps
    rows = await handler.list_records(TABLE, None, limit=ps, offset=offset)
    items = [_orm_row_to_record(r) for r in rows]
    total = await handler.count_records(TABLE, None)
    return {
        "total": total,
        "page_num": pn,
        "page_size": ps,
        "items": items,
    }


async def upsert_session_affinity_policy(
    handler: DBHandler,
    request: SessionAffinityPolicyUpdateRequest,
) -> dict[str, Any]:
    """按请求中的 ``policy_name`` 查找一行更新；不存在则插入。"""
    existing = await handler.get(TABLE, {"policy_name": request.policy_name})

    now = utc_now()
    if existing is None:
        row_data = _non_none_request_fields(request)
        row_data["created_at"] = now
        row_data["updated_at"] = now
        record = await handler.create(TABLE, row_data)
        return _orm_row_to_record(record)

    patch = _non_none_request_fields(request)
    if not patch:
        return _orm_row_to_record(existing)
    patch["updated_at"] = now
    updated = await handler.update(
        TABLE, {"policy_name": request.policy_name}, patch
    )
    if updated is None:
        raise RuntimeError("session_affinity_policy update returned no row")
    return _orm_row_to_record(updated)
