# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Session 与 Agent Server Pod 映射（session_mapping）：基于 ``DBHandler`` 异步读写。

表结构见 ``SESSION_MAPPING_TABLE_DEF``；API 返回形态与 ``SessionMappingInfo`` 一致。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw.extensions.agent_client.core.utils import utc_now
from jiuwenclaw.extensions.agent_client.models.distributed_service_models import (
    SESSION_MAPPING_TABLE_DEF,
    SessionMappingInfo,
)


def _orm_row_to_mapping_record(obj: Any) -> dict[str, Any]:
    raw_data = getattr(obj, "data", None)
    data = raw_data if isinstance(raw_data, dict) else None
    payload = {
        "session_id": getattr(obj, "session_id"),
        "user_id": getattr(obj, "user_id", None),
        "group_id": getattr(obj, "group_id", None),
        "bot_id": getattr(obj, "bot_id", None),
        "agent_server_pod": getattr(obj, "agent_server_pod"),
        "create_time": getattr(obj, "create_time"),
        "last_active_time": getattr(obj, "last_active_time"),
        "ttl": int(getattr(obj, "ttl", 0)),
        "data": data,
        "created_at": getattr(obj, "created_at"),
        "updated_at": getattr(obj, "updated_at"),
    }
    return SessionMappingInfo(**payload).model_dump(mode="json")


async def list_session_mappings(
    handler: DBHandler,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    group_id: str | None = None,
    bot_id: str | None = None,
    page_num: int = 1,
    page_size: int = 10,
) -> dict[str, Any]:
    """按条件列出映射（传入的条件之间为 AND）。

    当前页使用 ``DBHandler.list_records`` 的 ``limit`` / ``offset``；
    ``total`` 使用 ``DBHandler.count_records``。
    """
    table = SESSION_MAPPING_TABLE_DEF.table_name
    filters: dict[str, Any] = {}
    if session_id:
        filters["session_id"] = session_id
    if user_id:
        filters["user_id"] = user_id
    if group_id:
        filters["group_id"] = group_id
    if bot_id:
        filters["bot_id"] = bot_id
    pn = max(1, page_num)
    ps = max(1, page_size)
    offset = (pn - 1) * ps
    rows = await handler.list_records(table, filters, limit=ps, offset=offset)
    items = [_orm_row_to_mapping_record(r) for r in rows]
    total = await handler.count_records(table, filters)
    return {
        "total": total,
        "page_num": pn,
        "page_size": ps,
        "items": items,
    }


async def get_session_mapping_detail(
    handler: DBHandler,
    *,
    session_id: str,
) -> dict[str, Any] | None:
    """按 ``session_id``（主键）查询一条。"""
    table = SESSION_MAPPING_TABLE_DEF.table_name
    row = await handler.get(table, {"session_id": session_id})
    if row is None:
        return None
    return _orm_row_to_mapping_record(row)


async def upsert_session_mapping_record(
    handler: DBHandler,
    *,
    session_id: str,
    agent_server_pod: str,
    ttl: int,
    user_id: str | None = None,
    group_id: str | None = None,
    bot_id: str | None = None,
    create_time: datetime | None = None,
    last_active_time: datetime | None = None,
) -> dict[str, Any]:
    """插入或更新一条 Session 映射（供网关同步或其它写入场景调用）。"""
    table = SESSION_MAPPING_TABLE_DEF.table_name
    existing = await handler.get(table, {"session_id": session_id})
    now = utc_now()

    if existing is None:
        ct = create_time or now
        lat = last_active_time or now
        row_data: dict[str, Any] = {
            "session_id": session_id,
            "user_id": user_id,
            "group_id": group_id,
            "bot_id": bot_id,
            "agent_server_pod": agent_server_pod,
            "create_time": ct,
            "last_active_time": lat,
            "ttl": ttl,
            "created_at": now,
            "updated_at": now,
        }
        record = await handler.create(table, row_data)
        return _orm_row_to_mapping_record(record)

    patch: dict[str, Any] = {
        "agent_server_pod": agent_server_pod,
        "ttl": ttl,
        "last_active_time": last_active_time or now,
        "updated_at": now,
    }
    if user_id is not None:
        patch["user_id"] = user_id
    if group_id is not None:
        patch["group_id"] = group_id
    if bot_id is not None:
        patch["bot_id"] = bot_id

    updated = await handler.update(table, {"session_id": session_id}, patch)
    if updated is None:
        raise RuntimeError("session_mapping update returned no row")
    return _orm_row_to_mapping_record(updated)
