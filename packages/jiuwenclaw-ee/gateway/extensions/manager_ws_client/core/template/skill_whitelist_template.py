# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Skill 白名单模板 WebSocket 同步：将 Claw Manager 下发的 skill_whitelist_templates 写入 Gateway 本地库。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from ...infrastructure.utils import assert_jiuwenclaw_id_matches_payload, utc_now
from ...models.template_models import SKILL_WHITELIST_TEMPLATE_TABLE_DEF
from ...schemas.template_schemas import SkillWhitelistTemplateUpdateRequest

_TABLE = SKILL_WHITELIST_TEMPLATE_TABLE_DEF.table_name


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
    if len(normalized) > 512:
        raise ValueError("skill_source must be at most 512 characters")
    return normalized


async def _get_row(handler: DBHandler, template_id: str) -> Any | None:
    tid = _normalize_template_id(template_id)
    return await handler.get(_TABLE, {"template_id": tid})


async def update_skill_whitelist_template(
    handler: DBHandler,
    template_id: str,
    request: SkillWhitelistTemplateUpdateRequest,
    *,
    existing: Any | None = None,
) -> dict[str, Any] | None:
    tid = _normalize_template_id(template_id)
    row = existing if existing is not None else await _get_row(handler, tid)
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
    updated = await handler.update(_TABLE, {"template_id": tid}, updates)
    if updated is None:
        return None
    return {"template_id": str(getattr(updated, "template_id", tid))}


async def delete_skill_whitelist_template(handler: DBHandler, template_id: str) -> bool:
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


async def apply_skill_whitelist_template_sync(
    handler: DBHandler,
    op: str,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """应用 Claw Manager 经 WebSocket 下发的 skill_whitelist_templates 变更。"""
    assert_jiuwenclaw_id_matches_payload(payload)

    if op == "create":
        template = payload.get("template")
        if not isinstance(template, dict):
            raise ValueError("skill_whitelist_templates.create requires template object")
        template_uuid = _normalize_template_id(template.get("template_id"))
        now = utc_now()
        row_data: dict[str, Any] = {
            "template_id": template_uuid,
            "template_name": str(template["template_name"]).strip(),
            "description": template.get("description"),
            "skill_id": _normalize_skill_id(str(template["skill_id"])),
            "skill_version": _normalize_skill_version(str(template["skill_version"])),
            "skill_source": _normalize_skill_source(str(template["skill_source"])),
            "enabled": bool(template.get("enabled", True)),
            "data": template.get("data"),
            "created_at": _parse_iso_datetime(template.get("created_at")) or now,
            "updated_at": _parse_iso_datetime(template.get("updated_at")) or now,
        }
        await handler.create(_TABLE, row_data)
        return {"template_id": template_uuid}

    if op == "update":
        template_id = payload.get("template_id")
        updates = payload.get("updates")
        if template_id is None:
            raise ValueError("skill_whitelist_templates.update requires template_id")
        if not isinstance(updates, dict) or not updates:
            raise ValueError("skill_whitelist_templates.update requires non-empty updates")
        req = SkillWhitelistTemplateUpdateRequest.model_validate(updates)
        tid = _normalize_template_id(template_id)
        existing = await _get_row(handler, tid)
        row = await update_skill_whitelist_template(
            handler, tid, req, existing=existing
        )
        if row is None:
            raise ValueError(f"skill whitelist template template_id={tid!r} not found")
        return None

    if op == "delete":
        template_id = payload.get("template_id")
        if template_id is None:
            raise ValueError("skill_whitelist_templates.delete requires template_id")
        tid = _normalize_template_id(template_id)
        deleted = await delete_skill_whitelist_template(handler, tid)
        if not deleted:
            raise ValueError(f"skill whitelist template template_id={tid!r} not found")
        return None

    raise ValueError(f"unsupported skill_whitelist_templates.op: {op!r}")
