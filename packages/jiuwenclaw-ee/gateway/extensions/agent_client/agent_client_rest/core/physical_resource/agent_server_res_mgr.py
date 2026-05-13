# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""实例资源配置（resource_config）持久化：基于 ``DBHandler`` 异步读写。

应用启动时由 ``agent_client_rest.app`` 的 lifespan 完成 ``connect`` 与
``init_table(RESOURCE_CONFIG_TABLE_DEF)``。
"""

from __future__ import annotations

from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ..utils import format_ts, utc_now
from ...models.physical_resource_models import (
    RESOURCE_CONFIG_TABLE_DEF,
)
from ...schemas.physical_resource_schemas import (
    ResourceConfigUpdateRequest,
)


def _parse_cpu_m(value: str) -> int:
    val = value.strip().lower()
    if val.endswith("m"):
        return int(val[:-1])
    return int(float(val) * 1000)


def _parse_memory_mi(value: str) -> int:
    val = value.strip().lower()
    if val.endswith("gi"):
        return int(float(val[:-2]) * 1024)
    if val.endswith("mi"):
        return int(float(val[:-2]))
    raise ValueError("memory/storage only supports Mi/Gi")


_CREATE_REQUIRED_STRING_FIELDS = (
    "cpu_request",
    "cpu_limit",
    "memory_request",
    "memory_limit",
)


def _validate_create_resource_fields(request: ResourceConfigUpdateRequest) -> None:
    """新建记录时 cpu/memory 四类字段须显式传入且非空（不做默认值填充）。"""
    for name in _CREATE_REQUIRED_STRING_FIELDS:
        raw = getattr(request, name)
        if raw is None or not str(raw).strip():
            raise ValueError(f"{name} is required when creating resource config")


def validate_resource_config(payload: dict[str, Any]) -> None:
    cpu_request = payload.get("cpu_request")
    cpu_limit = payload.get("cpu_limit")
    if cpu_request and cpu_limit and _parse_cpu_m(cpu_request) > _parse_cpu_m(cpu_limit):
        raise ValueError("cpu_request cannot be greater than cpu_limit")

    memory_request = payload.get("memory_request")
    memory_limit = payload.get("memory_limit")
    if (
        memory_request
        and memory_limit
        and _parse_memory_mi(memory_request) > _parse_memory_mi(memory_limit)
    ):
        raise ValueError("memory_request cannot be greater than memory_limit")


def _resource_row_to_api_dict(obj: Any) -> dict[str, Any]:
    return {
        "id": getattr(obj, "id"),
        "component": getattr(obj, "component"),
        "cpu_request": getattr(obj, "cpu_request"),
        "cpu_limit": getattr(obj, "cpu_limit"),
        "memory_request": getattr(obj, "memory_request"),
        "memory_limit": getattr(obj, "memory_limit"),
        "storage_request": getattr(obj, "storage_request", None),
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
    }


def _put_response_subset(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "component": row["component"],
        "cpu_request": row.get("cpu_request"),
        "cpu_limit": row.get("cpu_limit"),
        "memory_request": row.get("memory_request"),
        "memory_limit": row.get("memory_limit"),
        "storage_request": row.get("storage_request"),
        "updated_at": row.get("updated_at"),
    }


async def upsert_resource_config_record(
    handler: DBHandler,
    request: ResourceConfigUpdateRequest,
) -> dict[str, Any]:
    """按 ``component`` 插入或更新一条资源配置；返回与 PUT 路由一致的响应字段子集。"""
    validate_resource_config(request.model_dump())
    now = utc_now()
    table = RESOURCE_CONFIG_TABLE_DEF.table_name

    existing = await handler.get(table, {"component": request.component})
    if existing is not None:
        patch: dict[str, Any] = {"updated_at": now}
        for field in (
            "cpu_request",
            "cpu_limit",
            "memory_request",
            "memory_limit",
            "storage_request",
        ):
            value = getattr(request, field)
            if value is not None:
                patch[field] = value
        updated = await handler.update(table, {"component": request.component}, patch)
        if updated is None:
            raise RuntimeError("resource_config update returned no row")
        full = _resource_row_to_api_dict(updated)
        return _put_response_subset(full)

    _validate_create_resource_fields(request)
    row_data: dict[str, Any] = {
        "component": request.component,
        "cpu_request": str(request.cpu_request).strip(),
        "cpu_limit": str(request.cpu_limit).strip(),
        "memory_request": str(request.memory_request).strip(),
        "memory_limit": str(request.memory_limit).strip(),
        "storage_request": request.storage_request,
        "created_at": now,
        "updated_at": now,
    }
    record = await handler.create(table, row_data)
    full = _resource_row_to_api_dict(record)
    return _put_response_subset(full)


async def list_resource_config_records(
    handler: DBHandler,
    component: str | None = None,
    *,
    page_num: int = 1,
    page_size: int = 10,
) -> list[dict[str, Any]]:
    """列出资源配置；``page_num`` 从 1 开始，``offset = (page_num - 1) * page_size``。"""
    filters: dict[str, Any] | None = None
    if component:
        filters = {"component": component}
    pn = max(1, page_num)
    ps = max(1, page_size)
    offset = (pn - 1) * ps
    rows = await handler.list_records(
        RESOURCE_CONFIG_TABLE_DEF.table_name,
        filters,
        limit=ps,
        offset=offset,
    )
    return [_resource_row_to_api_dict(r) for r in rows]
