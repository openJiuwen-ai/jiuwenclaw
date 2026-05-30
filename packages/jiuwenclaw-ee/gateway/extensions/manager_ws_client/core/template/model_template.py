# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""模型模板 WebSocket 同步：将 Claw Manager 下发的 model_templates 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.db import ensure_db_handler
from ...infrastructure.utils import utc_now
from ...models.template_models import MODEL_TEMPLATE_TABLE_DEF
from ...schemas.template_schemas import ModelTemplateUpdateRequest

_TABLE = MODEL_TEMPLATE_TABLE_DEF.table_name
_ALLOWED_MODEL_TYPES = frozenset({"default", "video", "audio", "vision"})
logger = logging.getLogger(__name__)


def _normalize_template_id(template_id: Any) -> str:
    normalized = str(template_id or "").strip()
    if not normalized:
        raise ValueError("template_id is required")
    if len(normalized) > 100:
        raise ValueError("template_id must be at most 100 characters")
    return normalized


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


async def _get_row(handler: DBHandler, template_id: str) -> Any | None:
    tid = _normalize_template_id(template_id)
    return await handler.get(_TABLE, {"template_id": tid})


async def update_model_template(
    handler: DBHandler,
    template_id: str,
    request: ModelTemplateUpdateRequest,
    *,
    existing: Any | None = None,
) -> dict[str, Any] | None:
    tid = _normalize_template_id(template_id)
    row = existing if existing is not None else await _get_row(handler, tid)
    if row is None:
        return None

    updates = request.model_dump(exclude_unset=True)
    if "model_type" in updates and updates["model_type"] is not None:
        updates["model_type"] = _validate_model_type(updates["model_type"])
    if "template_name" in updates and updates["template_name"] is not None:
        updates["template_name"] = updates["template_name"].strip()
    if "api_base" in updates and updates["api_base"] is not None:
        updates["api_base"] = updates["api_base"].strip()
    if "model_id" in updates and updates["model_id"] is not None:
        updates["model_id"] = updates["model_id"].strip()
    if "model_provider" in updates and updates["model_provider"] is not None:
        updates["model_provider"] = updates["model_provider"].strip()

    if not updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    updates["updated_at"] = utc_now()
    updated = await handler.update(_TABLE, {"template_id": tid}, updates)
    if updated is None:
        return None
    return {"template_id": str(getattr(updated, "template_id", tid))}


async def delete_model_template(handler: DBHandler, template_id: str) -> bool:
    tid = _normalize_template_id(template_id)
    existing = await _get_row(handler, tid)
    if existing is None:
        return False
    return await handler.delete(_TABLE, {"template_id": tid})


def _parse_iso_datetime(value: Any) -> Any:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    return value


async def apply_model_template(payload: dict[str, Any]) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 model_templates 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("model_templates.op is required")

    handler = await ensure_db_handler()

    if op == "create":
        template = payload.get("template")
        if not isinstance(template, dict):
            raise ValueError("model_templates.create requires template object")
        template_uuid = _normalize_template_id(template.get("template_id"))
        model_type = _validate_model_type(template["model_type"])
        now = utc_now()
        row_data: dict[str, Any] = {
            "template_id": template_uuid,
            "template_name": str(template["template_name"]).strip(),
            "description": template.get("description"),
            "model_type": model_type,
            "model_tags": template.get("model_tags"),
            "api_base": str(template["api_base"]).strip(),
            "api_key": template["api_key"],
            "model_id": str(template["model_id"]).strip(),
            "model_provider": str(template["model_provider"]).strip(),
            "parameters": template.get("parameters"),
            "timeout": int(template.get("timeout", 60)),
            "retry_count": int(template.get("retry_count", 3)),
            "enable_streaming": bool(template.get("enable_streaming", True)),
            "enable_function_calling": bool(template.get("enable_function_calling", True)),
            "verify_ssl": bool(template.get("verify_ssl", False)),
            "enabled": bool(template.get("enabled", True)),
            "data": template.get("data"),
            "created_at": _parse_iso_datetime(template.get("created_at")) or now,
            "updated_at": _parse_iso_datetime(template.get("updated_at")) or now,
        }
        await handler.create(_TABLE, row_data)
        result: dict[str, Any] | None = {"template_id": template_uuid}

    elif op == "update":
        template_id = payload.get("template_id")
        updates = payload.get("updates")
        if template_id is None:
            raise ValueError("model_templates.update requires template_id")
        if not isinstance(updates, dict) or not updates:
            raise ValueError("model_templates.update requires non-empty updates")
        req = ModelTemplateUpdateRequest.model_validate(updates)
        tid = _normalize_template_id(template_id)
        row = await update_model_template(handler, tid, req)
        if row is None:
            raise ValueError(f"model template template_id={tid!r} not found")
        result = None

    elif op == "delete":
        template_id = payload.get("template_id")
        if template_id is None:
            raise ValueError("model_templates.delete requires template_id")
        tid = _normalize_template_id(template_id)
        deleted = await delete_model_template(handler, tid)
        if not deleted:
            raise ValueError(f"model template template_id={tid!r} not found")
        result = None

    else:
        raise ValueError(f"unsupported model_templates.op: {op!r}")

    logger.info(
        "[ManagerWsClient] model_templates sync op=%s template_id=%s",
        op,
        (result or {}).get("template_id")
        or payload.get("template_id")
        or (payload.get("template") or {}).get("id"),
    )
    return result
