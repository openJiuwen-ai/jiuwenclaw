# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Skill 白名单模板 WebSocket 同步：将 Claw Manager 下发的 skill_whitelist_templates 写入 Gateway 本地库。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.db import ensure_db_handler
from ...infrastructure.utils import get_jiuwenclaw_id, parse_iso_datetime, utc_now
from ...models.template_models import SKILL_WHITELIST_TEMPLATE_TABLE_DEF
from ...schemas.template_schemas import SkillWhitelistTemplateUpdateRequest

_TABLE = SKILL_WHITELIST_TEMPLATE_TABLE_DEF.table_name
logger = logging.getLogger(__name__)


def _normalize_template_id(template_id: Any) -> str:
    normalized = str(template_id or "").strip()
    if not normalized:
        raise ValueError("template_id is required")
    if len(normalized) > 100:
        raise ValueError("template_id must be at most 100 characters")
    return normalized


def _normalize_skill_id(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("skill_id is required")
    if len(normalized) > 512:
        raise ValueError("skill_id must be at most 512 characters")
    return normalized


def _normalize_skill_version(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("skill_version is required")
    if len(normalized) > 64:
        raise ValueError("skill_version must be at most 64 characters")
    return normalized


def _normalize_skill_source(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("skill_source is required")
    if len(normalized) > 2048:
        raise ValueError("skill_source must be at most 2048 characters")
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


async def update_skill_whitelist_template(
    handler: DBHandler,
    template_id: str,
    request: SkillWhitelistTemplateUpdateRequest,
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
    if "template_name" in updates and updates["template_name"] is not None:
        updates["template_name"] = updates["template_name"].strip()
    if "skill_id" in updates and updates["skill_id"] is not None:
        updates["skill_id"] = _normalize_skill_id(updates["skill_id"])
    if "skill_version" in updates and updates["skill_version"] is not None:
        updates["skill_version"] = _normalize_skill_version(updates["skill_version"])
    if "skill_source" in updates and updates["skill_source"] is not None:
        updates["skill_source"] = _normalize_skill_source(updates["skill_source"])

    if not updates:
        raise ValueError("请求未包含任何可更新的业务字段")

    updates["updated_at"] = utc_now()
    updated = await handler.update(_TABLE, _template_pk(jiuwenclaw_id, tid), updates)
    if updated is None:
        return None
    return {"template_id": str(getattr(updated, "template_id", tid))}


async def delete_skill_whitelist_template(handler: DBHandler, template_id: str) -> bool:
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
    return {
        "jiuwenclaw_id": jiuwenclaw_id,
        "template_id": template_uuid,
        "template_name": str(template["template_name"]).strip(),
        "description": template.get("description"),
        "skill_id": _normalize_skill_id(str(template["skill_id"])),
        "skill_version": _normalize_skill_version(str(template["skill_version"])),
        "skill_source": _normalize_skill_source(str(template["skill_source"])),
        "enabled": bool(template.get("enabled", True)),
        "data": template.get("data"),
        "created_at": parse_iso_datetime(template.get("created_at")) or now,
        "updated_at": parse_iso_datetime(template.get("updated_at")) or now,
    }


async def _upsert_skill_whitelist_template_from_sync(
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


async def _sync_skill_whitelist_templates_records(
    handler: DBHandler,
    templates: list[dict[str, Any]],
    *,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    incoming_ids: set[str] = set()
    synced = 0
    for item in templates:
        if not isinstance(item, dict):
            raise ValueError("skill_whitelist_templates.sync templates must be objects")
        tid = _normalize_template_id(item.get("template_id"))
        incoming_ids.add(tid)
        await _upsert_skill_whitelist_template_from_sync(
            handler, item, jiuwenclaw_id=jiuwenclaw_id
        )
        synced += 1
    deleted = 0
    existing_rows = await handler.list_records(_TABLE, {"jiuwenclaw_id": jiuwenclaw_id})
    for row in existing_rows:
        tid = str(getattr(row, "template_id", "") or "")
        if tid and tid not in incoming_ids:
            if await delete_skill_whitelist_template(handler, tid):
                deleted += 1
    return {"synced_count": synced, "deleted_count": deleted}


async def apply_skill_whitelist_template(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 skill_whitelist_templates 变更。"""
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("skill_whitelist_templates.op is required")

    jiuwenclaw_id = get_jiuwenclaw_id()
    if not jiuwenclaw_id:
        raise ValueError("jiuwenclaw_id is not set")
    handler = await ensure_db_handler()

    if op == "create":
        template = payload.get("template")
        if not isinstance(template, dict):
            raise ValueError("skill_whitelist_templates.create requires template object")
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
            raise ValueError("skill_whitelist_templates.update requires template_id")
        if not isinstance(updates, dict) or not updates:
            raise ValueError("skill_whitelist_templates.update requires non-empty updates")
        req = SkillWhitelistTemplateUpdateRequest.model_validate(updates)
        tid = _normalize_template_id(template_id)
        existing = await _get_row_for_instance(handler, tid, jiuwenclaw_id)
        row = await update_skill_whitelist_template(
            handler, tid, req, existing=existing
        )
        if row is None:
            raise ValueError(f"skill whitelist template template_id={tid!r} not found")
        result = None

    elif op == "delete":
        template_id = payload.get("template_id")
        if template_id is None:
            raise ValueError("skill_whitelist_templates.delete requires template_id")
        tid = _normalize_template_id(template_id)
        deleted = await delete_skill_whitelist_template(handler, tid)
        if not deleted:
            raise ValueError(f"skill whitelist template template_id={tid!r} not found")
        result = None

    elif op == "sync":
        templates = payload.get("templates")
        if not isinstance(templates, list):
            raise ValueError("skill_whitelist_templates.sync requires templates array")
        result = await _sync_skill_whitelist_templates_records(
            handler, templates, jiuwenclaw_id=jiuwenclaw_id
        )

    else:
        raise ValueError(f"unsupported skill_whitelist_templates.op: {op!r}")

    logger.info(
        "[ManagerWsClient] skill_whitelist_templates sync op=%s template_id=%s",
        op,
        (result or {}).get("template_id")
        or payload.get("template_id")
        or (payload.get("template") or {}).get("template_id"),
    )
    return result
