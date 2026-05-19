# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""模型配置（model_config）持久化：基于 ``DBHandler`` 异步读写。

应用启动时由 ``agent_client_rest.app`` 的 lifespan 完成 ``connect`` 与
``init_table(MODEL_CONFIG_TABLE_DEF)``，本模块不再在每次请求中重复建表/连接。
"""

from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import format_ts, utc_now
from ...models.application_config_models import (
    MODEL_CONFIG_TABLE_DEF,
)
from ...schemas.application_config_schemas import (
    ModelConfigCreateRequest,
    ModelConfigUpdateRequest,
)


def _orm_row_to_dict(obj: Any) -> dict[str, Any]:
    return {
        "id": getattr(obj, "id"),
        "model_name": getattr(obj, "model_name"),
        "model_type": getattr(obj, "model_type"),
        "api_endpoint": getattr(obj, "api_endpoint"),
        "api_key_ref": getattr(obj, "api_key_ref"),
        "parameters": getattr(obj, "parameters", None),
        "rate_limit": getattr(obj, "rate_limit", None),
        "enabled": getattr(obj, "enabled"),
        "data": getattr(obj, "data", None),
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
    }


async def create_model_config_record(
    handler: DBHandler,
    request: ModelConfigCreateRequest,
) -> dict[str, Any]:
    """插入一条模型配置，返回与原先 JSON 存储一致的字典结构（含 ``id``、时间字符串）。"""
    now = utc_now()
    row_data: dict[str, Any] = {
        "model_name": request.model_name,
        "model_type": request.model_type,
        "api_endpoint": request.api_endpoint,
        "api_key_ref": request.api_key_ref,
        "parameters": request.parameters,
        "rate_limit": request.rate_limit,
        "enabled": request.enabled,
        "data": request.data,
        "created_at": now,
        "updated_at": now,
    }
    record = await handler.create(MODEL_CONFIG_TABLE_DEF.table_name, row_data)
    d = _orm_row_to_dict(record)
    return d


async def list_model_config_records(
    handler: DBHandler,
    model_type: str | None = None,
    enabled: bool | None = None,
    *,
    page_size: int = 10,
    page_num: int = 1,
) -> dict[str, Any]:
    """分页列出模型配置（全字段）；``limit=page_size``，``offset=(page_num-1)*page_size``。"""
    filters: dict[str, Any] = {}
    if model_type:
        filters["model_type"] = model_type
    if enabled is not None:
        filters["enabled"] = enabled
    limit = page_size
    offset = (page_num - 1) * page_size
    rows = await handler.list_records(
        MODEL_CONFIG_TABLE_DEF.table_name,
        filters,
        limit=limit,
        offset=offset,
    )
    total = await handler.count_records(
        MODEL_CONFIG_TABLE_DEF.table_name,
        filters,
    )
    items = [_orm_row_to_dict(r) for r in rows]
    return {"items": items, "total": total}


async def update_model_config_record(
    handler: DBHandler,
    model_id: int,
    request: ModelConfigUpdateRequest,
) -> None:
    """仅写请求体中显式给出的业务字段；未携带任何业务字段时抛出 ``RuntimeError``。

    失败时抛出 ``RuntimeError``（路由统一映射为 HTTP 500）。
    """
    existing = await handler.get(MODEL_CONFIG_TABLE_DEF.table_name, {"id": model_id})
    if existing is None:
        raise RuntimeError(f"model_config 不存在，id={model_id}")

    patch: dict[str, Any] = {}
    if request.model_name is not None:
        patch["model_name"] = request.model_name
    if request.model_type is not None:
        patch["model_type"] = request.model_type
    if request.api_endpoint is not None:
        patch["api_endpoint"] = request.api_endpoint
    if request.api_key_ref is not None:
        patch["api_key_ref"] = request.api_key_ref
    if request.parameters is not None:
        patch["parameters"] = request.parameters
    if request.rate_limit is not None:
        patch["rate_limit"] = request.rate_limit
    if request.enabled is not None:
        patch["enabled"] = request.enabled
    if request.data is not None:
        patch["data"] = request.data

    if not patch:
        raise RuntimeError("请求未包含任何可更新的业务字段，无数据变化")

    patch["updated_at"] = utc_now()
    updated = await handler.update(
        MODEL_CONFIG_TABLE_DEF.table_name,
        {"id": model_id},
        patch,
    )
    if updated is None:
        raise RuntimeError(
            f"model_config 更新后未能读回行，id={model_id}（可能已被并发删除）"
        )


async def delete_model_config_record(handler: DBHandler, model_id: int) -> bool:
    return await handler.delete(MODEL_CONFIG_TABLE_DEF.table_name, {"id": model_id})


def row_dict_to_create_request(row: dict[str, Any]) -> ModelConfigCreateRequest:
    """将 CSV/JSON 解析后的单行字典转为创建请求。"""
    raw_data = row.get("data") if isinstance(row.get("data"), dict) else {}
    return ModelConfigCreateRequest(
        model_name=str(row.get("model_name") or "").strip(),
        model_type=str(row.get("model_type") or "").strip(),
        api_endpoint=str(row.get("api_endpoint") or "").strip(),
        api_key_ref=str(row.get("api_key_ref") or "").strip(),
        parameters=row.get("parameters") if isinstance(row.get("parameters"), dict) else {},
        rate_limit=row.get("rate_limit") if isinstance(row.get("rate_limit"), dict) else {},
        enabled=bool(row.get("enabled", True)),
        data=raw_data if raw_data else None,
    )


def _parse_import_file(file_name: str, text: str) -> list[dict[str, Any]]:
    lower_name = file_name.lower()
    if lower_name.endswith(".json"):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("JSON file must be an array")
        return [item for item in data if isinstance(item, dict)]
    if lower_name.endswith(".csv"):
        reader = csv.DictReader(StringIO(text))
        rows: list[dict[str, Any]] = []
        for row in reader:
            mapped = dict(row)
            if "enabled" in mapped:
                mapped["enabled"] = str(mapped["enabled"]).strip().lower() in {"1", "true", "yes", "y"}
            rows.append(mapped)
        return rows
    raise ValueError("file must be .csv or .json")


async def batch_import_model_configs(
    handler: DBHandler,
    file_name: str,
    raw: bytes,
) -> dict[str, Any]:
    """解析 multipart CSV/JSON 后批量写入 ``model_config`` 表；每条独立提交，失败行记入 ``errors``。"""
    text = raw.decode("utf-8", errors="replace")
    rows = _parse_import_file(file_name, text)
    success = 0
    errors: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        try:
            req = row_dict_to_create_request(row)
            await create_model_config_record(handler, req)
            success += 1
        except Exception as exc:
            errors.append({"row": idx, "error": str(exc)})
    return {
        "total": len(rows),
        "success": success,
        "failed": len(rows) - success,
        "errors": errors,
    }
