# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""模型模板 WebSocket 同步：将 Claw Manager 下发的 model_templates 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.db import ensure_db_handler
from ...infrastructure.utils import get_jiuwenclaw_id, parse_iso_datetime, utc_now
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


def _normalize_model_types(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value] if value.strip() else []
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError("model_type must be a list of strings")
    normalized: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ValueError("model_type must be a list of strings")
        text = item.strip()
        if not text or text in normalized:
            continue
        if text not in _ALLOWED_MODEL_TYPES:
            raise ValueError(
                f"model_type entries must be in {sorted(_ALLOWED_MODEL_TYPES)}, got {text!r}"
            )
        normalized.append(text)
    return normalized


def _template_pk(jiuwenclaw_id: str, template_id: str) -> dict[str, str]:
    return {
        "jiuwenclaw_id": jiuwenclaw_id,
        "template_id": _normalize_template_id(template_id),
    }


async def _get_row_for_instance(
    handler: DBHandler,
    template_id: str,
    jiuwenclaw_id: str,
) -> Any | None:
    return await handler.get(_TABLE, _template_pk(jiuwenclaw_id, template_id))


async def update_model_template(
    handler: DBHandler,
    template_id: str,
    request: ModelTemplateUpdateRequest,
    *,
    existing: Any | None = None,
) -> dict[str, Any] | None:
    jiuwenclaw_id = get_jiuwenclaw_id()
    tid = _normalize_template_id(template_id)
    row = existing if existing is not None else await _get_row_for_instance(
        handler, tid, jiuwenclaw_id
    )
    if row is None:
        return None

    updates = request.model_dump(exclude_unset=True)
    if "model_type" in updates and updates["model_type"] is not None:
        updates["model_type"] = _normalize_model_types(updates["model_type"])
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
    updated = await handler.update(_TABLE, _template_pk(jiuwenclaw_id, tid), updates)
    if updated is None:
        return None
    return {"template_id": str(getattr(updated, "template_id", tid))}


async def delete_model_template(handler: DBHandler, template_id: str) -> bool:
    jiuwenclaw_id = get_jiuwenclaw_id()
    tid = _normalize_template_id(template_id)
    existing = await _get_row_for_instance(handler, tid, jiuwenclaw_id)
    if existing is None:
        return False
    return await handler.delete(_TABLE, _template_pk(jiuwenclaw_id, tid))


def _build_row_from_template(
    template: dict[str, Any],
    *,
    jiuwenclaw_id: str,
    now: Any,
) -> dict[str, Any]:
    template_uuid = _normalize_template_id(template.get("template_id"))
    model_type = _normalize_model_types(template.get("model_type"))
    return {
        "jiuwenclaw_id": jiuwenclaw_id,
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
        "created_at": parse_iso_datetime(template.get("created_at")) or now,
        "updated_at": parse_iso_datetime(template.get("updated_at")) or now,
    }


async def _upsert_model_template_from_sync(
    handler: DBHandler,
    template: dict[str, Any],
    *,
    jiuwenclaw_id: str,
) -> None:
    now = utc_now()
    tid = _normalize_template_id(template.get("template_id"))
    existing = await _get_row_for_instance(handler, tid, jiuwenclaw_id)
    row_data = _build_row_from_template(
        template, jiuwenclaw_id=jiuwenclaw_id, now=now
    )
    if existing is None:
        await handler.create(_TABLE, row_data)
        return
    created_at = getattr(existing, "created_at", None)
    if created_at is not None:
        row_data["created_at"] = created_at
    updates = {
        k: v for k, v in row_data.items() if k not in ("jiuwenclaw_id", "template_id")
    }
    updates["updated_at"] = utc_now()
    await handler.update(_TABLE, _template_pk(jiuwenclaw_id, tid), updates)


async def _sync_model_templates_records(
    handler: DBHandler,
    templates: list[dict[str, Any]],
    *,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    incoming_ids: set[str] = set()
    synced = 0
    for item in templates:
        if not isinstance(item, dict):
            raise ValueError("model_templates.sync templates must be objects")
        tid = _normalize_template_id(item.get("template_id"))
        incoming_ids.add(tid)
        await _upsert_model_template_from_sync(handler, item, jiuwenclaw_id=jiuwenclaw_id)
        synced += 1
    deleted = 0
    existing_rows = await handler.list_records(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
    for row in existing_rows:
        tid = str(getattr(row, "template_id", "") or "")
        if tid and tid not in incoming_ids:
            if await delete_model_template(handler, tid):
                deleted += 1
    return {"synced_count": synced, "deleted_count": deleted}


async def apply_model_template(payload: dict[str, Any]) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 model_templates 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("model_templates.op is required")

    jiuwenclaw_id = get_jiuwenclaw_id()
    if not jiuwenclaw_id:
        raise ValueError("jiuwenclaw_id is not set")
    handler = await ensure_db_handler()

    if op == "create":
        template = payload.get("template")
        if not isinstance(template, dict):
            raise ValueError("model_templates.create requires template object")
        now = utc_now()
        row_data = _build_row_from_template(
            template, jiuwenclaw_id=jiuwenclaw_id, now=now
        )
        await handler.create(_TABLE, row_data)
        result: dict[str, Any] | None = {"template_id": row_data["template_id"]}

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

    elif op == "sync":
        templates = payload.get("templates")
        if not isinstance(templates, list):
            raise ValueError("model_templates.sync requires templates array")
        result = await _sync_model_templates_records(
            handler, templates, jiuwenclaw_id=jiuwenclaw_id
        )

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
