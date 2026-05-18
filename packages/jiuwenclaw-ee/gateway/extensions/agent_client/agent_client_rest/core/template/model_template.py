# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""模型模板 ``model_template`` 持久化：基于 ``DBHandler`` 写入 Gateway 本地库。"""

from __future__ import annotations

import os
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ..utils import format_ts, utc_now
from ...models.template_models import MODEL_TEMPLATE_TABLE_DEF
from ...schemas.template_schemas import (
    ModelTemplateCreateRequest,
    ModelTemplateUpdateRequest,
)

_TABLE = MODEL_TEMPLATE_TABLE_DEF.table_name
_ALLOWED_MODEL_TYPES = frozenset({"default", "video", "audio", "vision"})
_LIST_ALL_CAP = 10_000


def resolve_jiuwenclaw_id() -> str:
    instance_id = os.getenv("JIUWENCLAW_PROVISIONED_INSTANCE_ID", "").strip()
    if not instance_id:
        raise ValueError("JIUWENCLAW_PROVISIONED_INSTANCE_ID is not set")
    return instance_id


def _validate_model_type(value: str | list[str]) -> str | list[str]:
    if isinstance(value, str):
        if value not in _ALLOWED_MODEL_TYPES:
            raise ValueError(
                f"model_type must be one of {sorted(_ALLOWED_MODEL_TYPES)}, got {value!r}"
            )
        return value
    if isinstance(value, list):
        if not value:
            raise ValueError("model_type list cannot be empty")
        for item in value:
            if item not in _ALLOWED_MODEL_TYPES:
                raise ValueError(
                    f"model_type entries must be in {sorted(_ALLOWED_MODEL_TYPES)}, got {item!r}"
                )
        return value
    raise ValueError("model_type must be a string or a list of strings")


def _matches_model_type(row_model_type: Any, filter_type: str) -> bool:
    if isinstance(row_model_type, str):
        return row_model_type == filter_type
    if isinstance(row_model_type, list):
        return filter_type in row_model_type
    return str(row_model_type) == filter_type


def _row_to_dict(obj: Any) -> dict[str, Any]:
    model_type = getattr(obj, "model_type", None)
    if model_type is not None and not isinstance(model_type, (str, list)):
        model_type = str(model_type)
    model_tags = getattr(obj, "model_tags", None)
    if model_tags is not None and not isinstance(model_tags, list):
        model_tags = list(model_tags) if model_tags else None
    return {
        "id": getattr(obj, "id"),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id"),
        "display_name": getattr(obj, "display_name"),
        "description": getattr(obj, "description"),
        "model_type": model_type,
        "model_tags": model_tags,
        "api_base": getattr(obj, "api_base"),
        "api_key": getattr(obj, "api_key"),
        "model_id": getattr(obj, "model_id"),
        "model_provider": getattr(obj, "model_provider"),
        "parameters": getattr(obj, "parameters", None),
        "timeout": getattr(obj, "timeout"),
        "retry_count": getattr(obj, "retry_count"),
        "enable_streaming": getattr(obj, "enable_streaming"),
        "enable_function_calling": getattr(obj, "enable_function_calling"),
        "verify_ssl": getattr(obj, "verify_ssl"),
        "enabled": getattr(obj, "enabled"),
        "data": getattr(obj, "data", None),
        "created_at": format_ts(getattr(obj, "created_at", None)),
        "updated_at": format_ts(getattr(obj, "updated_at", None)),
    }


async def _get_row_for_instance(
    handler: DBHandler,
    template_id: int,
    jiuwenclaw_id: str,
) -> Any | None:
    row = await handler.get(_TABLE, {"id": template_id})
    if row is None:
        return None
    if getattr(row, "jiuwenclaw_id", None) != jiuwenclaw_id:
        return None
    return row


async def create_model_template(
    handler: DBHandler,
    request: ModelTemplateCreateRequest,
) -> dict[str, Any]:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    model_type = _validate_model_type(request.model_type)
    now = utc_now()
    row_data: dict[str, Any] = {
        "jiuwenclaw_id": jiuwenclaw_id,
        "display_name": request.display_name,
        "description": request.description,
        "model_type": model_type,
        "model_tags": request.model_tags,
        "api_base": request.api_base,
        "api_key": request.api_key,
        "model_id": request.model_id,
        "model_provider": request.model_provider,
        "parameters": request.parameters,
        "timeout": request.timeout,
        "retry_count": request.retry_count,
        "enable_streaming": request.enable_streaming,
        "enable_function_calling": request.enable_function_calling,
        "verify_ssl": request.verify_ssl,
        "enabled": request.enabled,
        "data": request.data,
        "created_at": now,
        "updated_at": now,
    }
    record = await handler.create(_TABLE, row_data)
    return _row_to_dict(record)


async def list_model_templates(
    handler: DBHandler,
    *,
    page_num: int,
    page_size: int,
    enabled: bool | None,
    model_type: str | None,
) -> dict[str, Any]:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    filters: dict[str, Any] = {"jiuwenclaw_id": jiuwenclaw_id}
    if enabled is not None:
        filters["enabled"] = enabled

    page_num = max(page_num, 1)
    page_size = min(max(page_size, 1), 200)

    if model_type:
        rows = await handler.list_records(
            _TABLE, filters, limit=_LIST_ALL_CAP, offset=0
        )
        items = [
            _row_to_dict(r)
            for r in rows
            if _matches_model_type(getattr(r, "model_type", None), model_type)
        ]
        total = len(items)
        offset = (page_num - 1) * page_size
        page_items = items[offset : offset + page_size]
        return {
            "items": page_items,
            "total": total,
            "page": page_num,
            "page_size": page_size,
        }

    offset = (page_num - 1) * page_size
    rows = await handler.list_records(
        _TABLE, filters, limit=page_size, offset=offset
    )
    total = await handler.count_records(_TABLE, filters)
    items = [_row_to_dict(r) for r in rows]
    return {
        "items": items,
        "total": total,
        "page": page_num,
        "page_size": page_size,
    }


async def get_model_template(
    handler: DBHandler,
    template_id: int,
) -> dict[str, Any] | None:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    row = await _get_row_for_instance(handler, template_id, jiuwenclaw_id)
    if row is None:
        return None
    return _row_to_dict(row)


async def update_model_template(
    handler: DBHandler,
    template_id: int,
    request: ModelTemplateUpdateRequest,
) -> dict[str, Any] | None:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    existing = await _get_row_for_instance(handler, template_id, jiuwenclaw_id)
    if existing is None:
        return None

    updates = request.model_dump(exclude_unset=True)
    if "model_type" in updates and updates["model_type"] is not None:
        updates["model_type"] = _validate_model_type(updates["model_type"])
    if "display_name" in updates and updates["display_name"] is not None:
        updates["display_name"] = updates["display_name"].strip()
    if "api_base" in updates and updates["api_base"] is not None:
        updates["api_base"] = updates["api_base"].strip()
    if "model_id" in updates and updates["model_id"] is not None:
        updates["model_id"] = updates["model_id"].strip()
    if "model_provider" in updates and updates["model_provider"] is not None:
        updates["model_provider"] = updates["model_provider"].strip()

    if not updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    updates["updated_at"] = utc_now()
    updated = await handler.update(_TABLE, {"id": template_id}, updates)
    if updated is None:
        return None
    return _row_to_dict(updated)


async def delete_model_template(handler: DBHandler, template_id: int) -> bool:
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    existing = await _get_row_for_instance(handler, template_id, jiuwenclaw_id)
    if existing is None:
        return False
    return await handler.delete(_TABLE, {"id": template_id})
