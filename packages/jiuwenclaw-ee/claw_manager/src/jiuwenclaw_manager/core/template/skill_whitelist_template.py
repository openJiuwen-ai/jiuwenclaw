"""Skill 白名单模板 skill_whitelist_template 业务逻辑。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.utils import iso_datetime, new_template_id, utc_now
from jiuwenclaw_manager.manager_ws_server.server import push_config_op_to_all
from jiuwenclaw_manager.models.template_models import SKILL_WHITELIST_TEMPLATE_TABLE_DEF
from jiuwenclaw_manager.schemas.template_schemas import (
    SkillWhitelistTemplateCreateBody,
    SkillWhitelistTemplateOut,
    SkillWhitelistTemplateUpdateBody,
)

_TABLE = SKILL_WHITELIST_TEMPLATE_TABLE_DEF.table_name
_CONFIG_SECTION = "skill_whitelist_templates"


async def push_skill_whitelist_templates_to_all_gateways(
    op: str,
    *,
    template: dict[str, Any] | None = None,
    template_id: str | None = None,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """向所有已注册 Gateway 推送 Skill 白名单模板变更。"""
    payload: dict[str, Any] = {"op": op}
    if template is not None:
        payload["template"] = template
    if template_id is not None:
        payload["template_id"] = template_id
    if updates is not None:
        payload["updates"] = updates
    return await push_config_op_to_all({_CONFIG_SECTION: payload})


def _template_pk(template_id: str) -> dict[str, Any]:
    return {"template_id": template_id.strip()}


def _normalize_template_id(template_id: str) -> str:
    normalized = template_id.strip()
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


def _row_to_out(row: Any) -> SkillWhitelistTemplateOut:
    return SkillWhitelistTemplateOut(
        id=row.id,
        template_id=str(row.template_id),
        template_name=row.template_name,
        description=row.description,
        skill_id=row.skill_id,
        skill_version=row.skill_version,
        skill_source=row.skill_source,
        enabled=row.enabled,
        data=row.data,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class SkillWhitelistTemplateService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def _db_update_template(
        self, template_id: str, updates: dict[str, Any]
    ) -> Any | None:
        if not updates:
            return await self._handler.get(_TABLE, _template_pk(template_id))
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        return await self._handler.update(
            _TABLE, _template_pk(template_id), payload
        )

    async def _db_delete_template(self, template_id: str) -> bool:
        return await self._handler.delete(_TABLE, _template_pk(template_id))

    @staticmethod
    def _template_dict_for_push(row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        return {
            "template_id": row["template_id"],
            "template_name": row["template_name"],
            "description": row.get("description"),
            "skill_id": row["skill_id"],
            "skill_version": row["skill_version"],
            "skill_source": row["skill_source"],
            "enabled": row.get("enabled"),
            "data": row.get("data"),
            "created_at": iso_datetime(row.get("created_at") or now),
            "updated_at": iso_datetime(row.get("updated_at") or now),
        }

    @staticmethod
    def _build_row_for_create(
        body: SkillWhitelistTemplateCreateBody, *, template_id: str
    ) -> dict[str, Any]:
        return {
            "template_id": template_id,
            "template_name": body.template_name.strip(),
            "description": body.description,
            "skill_id": _normalize_skill_id(body.skill_id),
            "skill_version": _normalize_skill_version(body.skill_version),
            "skill_source": _normalize_skill_source(body.skill_source),
            "enabled": body.enabled,
            "data": body.data,
        }

    @staticmethod
    def _normalize_updates(updates: dict[str, Any]) -> dict[str, Any]:
        if "template_name" in updates and updates["template_name"] is not None:
            updates["template_name"] = updates["template_name"].strip()
        if "skill_id" in updates and updates["skill_id"] is not None:
            updates["skill_id"] = _normalize_skill_id(updates["skill_id"])
        if "skill_version" in updates and updates["skill_version"] is not None:
            updates["skill_version"] = _normalize_skill_version(
                updates["skill_version"]
            )
        if "skill_source" in updates and updates["skill_source"] is not None:
            updates["skill_source"] = _normalize_skill_source(updates["skill_source"])
        return updates

    async def create(
        self,
        body: SkillWhitelistTemplateCreateBody,
    ) -> SkillWhitelistTemplateOut:
        template_uuid = new_template_id()
        row = self._build_row_for_create(body, template_id=template_uuid)
        now = utc_now()
        payload = dict(row)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        await push_skill_whitelist_templates_to_all_gateways(
            "create",
            template=self._template_dict_for_push(payload, now=now),
        )
        created = await self._handler.create(_TABLE, payload)
        return _row_to_out(created)

    async def get(self, template_id: str) -> SkillWhitelistTemplateOut | None:
        tid = _normalize_template_id(template_id)
        row = await self._handler.get(_TABLE, _template_pk(tid))
        if row is None:
            return None
        return _row_to_out(row)

    async def list_templates(
        self,
        *,
        page: int,
        page_size: int,
        enabled: bool | None,
        skill_id: str | None,
        skill_source: str | None,
    ) -> dict[str, Any]:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        filters: dict[str, Any] = {}
        if enabled is not None:
            filters["enabled"] = enabled
        if skill_id is not None:
            filters["skill_id"] = _normalize_skill_id(skill_id)
        if skill_source is not None:
            filters["skill_source"] = _normalize_skill_source(skill_source)

        offset = (page - 1) * page_size
        rows = await self._handler.list_records(
            _TABLE, filters, limit=page_size, offset=offset
        )
        total = await self._handler.count_records(_TABLE, filters)
        items = [_row_to_out(r).model_dump(mode="json") for r in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def update(
        self,
        template_id: str,
        body: SkillWhitelistTemplateUpdateBody,
    ) -> SkillWhitelistTemplateOut | None:
        tid = _normalize_template_id(template_id)
        updates = body.model_dump(exclude_unset=True)

        if not updates:
            row = await self._handler.get(_TABLE, _template_pk(tid))
            return _row_to_out(row) if row is not None else None

        existing = await self._handler.get(_TABLE, _template_pk(tid))
        if existing is None:
            return None

        updates = self._normalize_updates(updates)

        await push_skill_whitelist_templates_to_all_gateways(
            "update",
            template_id=tid,
            updates=updates,
        )
        row = await self._db_update_template(tid, updates)
        if row is None:
            return None
        return _row_to_out(row)

    async def delete(self, template_id: str) -> bool:
        tid = _normalize_template_id(template_id)
        row = await self._handler.get(_TABLE, _template_pk(tid))
        if row is None:
            return False
        await push_skill_whitelist_templates_to_all_gateways(
            "delete",
            template_id=tid,
        )
        return await self._db_delete_template(tid)
