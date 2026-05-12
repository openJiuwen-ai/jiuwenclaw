# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""服务运行状态（service_status_view）读取：基于 ``DBHandler`` 异步访问。

表结构见 ``SERVICE_STATUS_VIEW_TABLE_DEF``。API 返回 ``ServiceStatusViewInfo`` 形态。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ..utils import utc_now
from ...models.distributed_service_models import (
    SERVICE_STATUS_VIEW_TABLE_DEF,
    ServiceStatusViewInfo,
)


def _orm_row_to_service_record(obj: Any) -> dict[str, Any]:
    raw_data = getattr(obj, "data", None)
    data = raw_data if isinstance(raw_data, dict) else None
    payload = {
        "pod_name": getattr(obj, "pod_name"),
        "component": getattr(obj, "component"),
        "status": getattr(obj, "status"),
        "cpu_usage": getattr(obj, "cpu_usage", None),
        "memory_usage": getattr(obj, "memory_usage", None),
        "restart_count": int(getattr(obj, "restart_count", 0)),
        "start_time": getattr(obj, "start_time", None),
        "ready": bool(getattr(obj, "ready")),
        "node_name": getattr(obj, "node_name", None),
        "data": data,
        "created_at": getattr(obj, "created_at"),
        "updated_at": getattr(obj, "updated_at"),
    }
    return ServiceStatusViewInfo(**payload).model_dump(mode="json")


async def list_agent_server_service_status(
    handler: DBHandler,
    *,
    component: str | None = None,
    status: str | None = None,
    page_num: int = 1,
    page_size: int = 10,
) -> list[dict[str, Any]]:
    """列出 Pod 状态；可按 ``component``、``status`` 过滤（AND）。

    ``page_num`` 从 1 开始；``limit = page_size``，``offset = (page_num - 1) * page_size``。
    默认 ``page_num=1``、``page_size=10``（即 ``limit=10``、``offset=0``）。
    """
    table = SERVICE_STATUS_VIEW_TABLE_DEF.table_name
    filters: dict[str, Any] | None = None
    if component or status:
        filters = {}
        if component:
            filters["component"] = component
        if status:
            filters["status"] = status
    pn = max(1, page_num)
    ps = max(1, page_size)
    offset = (pn - 1) * ps
    rows = await handler.list_records(
        table,
        filters,
        limit=ps,
        offset=offset,
    )
    return [_orm_row_to_service_record(r) for r in rows]


async def get_agent_server_service_detail(
    handler: DBHandler,
    *,
    pod_name: str,
) -> dict[str, Any] | None:
    """按 ``pod_name``（主键）查询一条；不存在返回 ``None``。"""
    table = SERVICE_STATUS_VIEW_TABLE_DEF.table_name
    row = await handler.get(table, {"pod_name": pod_name})
    if row is None:
        return {}
    return _orm_row_to_service_record(row)


async def upsert_service_status_record(
    handler: DBHandler,
    *,
    pod_name: str,
    component: str,
    status: str,
    cpu_usage: float | None = None,
    memory_usage: float | None = None,
    restart_count: int = 0,
    start_time: datetime | None = None,
    ready: bool = False,
    node_name: str | None = None,
) -> dict[str, Any]:
    """插入或更新一条 Pod 状态（供同步任务或其它路由写入库表）。"""
    table = SERVICE_STATUS_VIEW_TABLE_DEF.table_name
    existing = await handler.get(table, {"pod_name": pod_name})
    now = utc_now()
    payload: dict[str, Any] = {
        "pod_name": pod_name,
        "component": component,
        "status": status,
        "cpu_usage": cpu_usage,
        "memory_usage": memory_usage,
        "restart_count": restart_count,
        "start_time": start_time,
        "ready": ready,
        "node_name": node_name,
    }
    if existing is None:
        payload["created_at"] = now
        payload["updated_at"] = now
        record = await handler.create(table, payload)
    else:
        key = {"pod_name": pod_name}
        patch = {k: v for k, v in payload.items() if k != "pod_name"}
        patch["updated_at"] = now
        record = await handler.update(table, key, patch)
        if record is None:
            raise RuntimeError("service_status_view update returned no row")
    return _orm_row_to_service_record(record)
