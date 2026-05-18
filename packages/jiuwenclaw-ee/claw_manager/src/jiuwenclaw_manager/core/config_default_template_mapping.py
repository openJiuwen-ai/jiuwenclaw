"""用户与群组默认模板映射 config_default_template_mapping 业务逻辑。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.models.schemas import (
    ConfigDefaultTemplateMappingCreateBody,
    ConfigDefaultTemplateMappingOut,
    ConfigDefaultTemplateMappingUpdateBody,
)
from jiuwenclaw_manager.repositories.config_default_template_mapping_repo import (
    ConfigDefaultTemplateMappingRepository,
)
from jiuwenclaw_manager.repositories.instance_repo import InstanceRepository
from jiuwenclaw_manager.core.gateway_forward import (
    EnterpriseGatewayForward,
    forward_headers,
    forward_upstream_data,
    forward_upstream_data_or_none,
    normalize_list_page,
)

_ALLOWED_TEMPLATE_TYPES = frozenset({
    "model",
    "channel",
    "skill_whitelist",
    "service_resource",
})


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _optional_key(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _validate_template_type(template_type: str) -> str:
    normalized = template_type.strip()
    if normalized not in _ALLOWED_TEMPLATE_TYPES:
        raise ValueError(
            f"template_type must be one of {sorted(_ALLOWED_TEMPLATE_TYPES)}, got {template_type!r}"
        )
    return normalized


def _validate_dimension_keys(user_id: str | None, group_id: str | None) -> tuple[str | None, str | None]:
    uid = _optional_key(user_id)
    gid = _optional_key(group_id)
    if uid is None and gid is None:
        raise ValueError("at least one of user_id or group_id is required")
    return uid, gid


def _row_to_out(row: Any) -> ConfigDefaultTemplateMappingOut:
    return ConfigDefaultTemplateMappingOut(
        id=row.id,
        jiuwenclaw_id=row.jiuwenclaw_id,
        user_id=row.user_id,
        group_id=row.group_id,
        template_id=row.template_id,
        template_type=row.template_type,
        enabled=row.enabled,
        data=row.data,
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
    )


def _out_from_gateway(data: dict[str, Any]) -> ConfigDefaultTemplateMappingOut:
    return ConfigDefaultTemplateMappingOut.model_validate(data)


class ConfigDefaultTemplateMappingService:
    def __init__(
        self,
        handler: DBHandler,
        http_client: httpx.AsyncClient,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._handler = handler
        self._repo = ConfigDefaultTemplateMappingRepository(handler)
        self._instance_repo = InstanceRepository(handler)
        self._gw = EnterpriseGatewayForward(
            handler, http_client, extra_headers or forward_headers()
        )

    async def _validate_jiuwenclaw_id(self, jiuwenclaw_id: str) -> str:
        normalized = jiuwenclaw_id.strip()
        if not normalized:
            raise ValueError("jiuwenclaw_id is required")
        inst = await self._instance_repo.get(normalized)
        if inst is None:
            raise ValueError(f"unknown jiuwenclaw_id={normalized!r}")
        return normalized

    async def create(
        self, jiuwenclaw_id: str, body: ConfigDefaultTemplateMappingCreateBody
    ) -> ConfigDefaultTemplateMappingOut:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        user_id, group_id = _validate_dimension_keys(body.user_id, body.group_id)
        template_type = _validate_template_type(body.template_type)
        template_id = body.template_id.strip()
        if not template_id:
            raise ValueError("template_id is required")

        gw_data = forward_upstream_data(
            await self._gw.create_template_mapping(
                normalized, body.model_dump(mode="json")
            )
        )
        if not isinstance(gw_data, dict) or gw_data.get("id") is None:
            raise ValueError("gateway create template mapping returned no id")

        created = await self._repo.create(
            {
                "id": int(gw_data["id"]),
                "jiuwenclaw_id": normalized,
                "user_id": user_id,
                "group_id": group_id,
                "template_id": template_id,
                "template_type": template_type,
                "enabled": body.enabled,
                "data": body.data,
            }
        )
        return _row_to_out(created)

    async def get(
        self, jiuwenclaw_id: str, mapping_id: int
    ) -> ConfigDefaultTemplateMappingOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        gw_data = forward_upstream_data_or_none(
            await self._gw.get_template_mapping(normalized, mapping_id)
        )
        if gw_data is None:
            return None
        if not isinstance(gw_data, dict):
            raise ValueError("invalid gateway response for template mapping")
        return _out_from_gateway(gw_data)

    async def list_mappings(
        self,
        jiuwenclaw_id: str,
        *,
        page: int,
        page_size: int,
        user_id: str | None,
        group_id: str | None,
        template_type: str | None,
        template_id: str | None,
        enabled: bool | None,
    ) -> dict[str, Any]:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        if template_type:
            template_type = _validate_template_type(template_type)

        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        gw_data = forward_upstream_data(
            await self._gw.list_template_mappings(
                normalized,
                user_id=_optional_key(user_id),
                group_id=_optional_key(group_id),
                template_type=template_type,
                template_id=template_id.strip() if template_id else None,
                enabled=enabled,
                page_num=page,
                page_size=page_size,
            )
        )
        if not isinstance(gw_data, dict):
            return normalize_list_page(None, page=page, page_size=page_size)
        return normalize_list_page(gw_data, page=page, page_size=page_size)

    async def update(
        self,
        jiuwenclaw_id: str,
        mapping_id: int,
        body: ConfigDefaultTemplateMappingUpdateBody,
    ) -> ConfigDefaultTemplateMappingOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)

        updates = body.model_dump(exclude_unset=True)
        if "template_type" in updates and updates["template_type"] is not None:
            updates["template_type"] = _validate_template_type(updates["template_type"])
        if "template_id" in updates and updates["template_id"] is not None:
            updates["template_id"] = updates["template_id"].strip()
            if not updates["template_id"]:
                raise ValueError("template_id cannot be empty")
        if "user_id" in updates:
            updates["user_id"] = _optional_key(updates["user_id"])
        if "group_id" in updates:
            updates["group_id"] = _optional_key(updates["group_id"])

        row = await self._repo.get(normalized, mapping_id)
        merged_user = updates.get("user_id", row.user_id if row else None)
        merged_group = updates.get("group_id", row.group_id if row else None)
        _validate_dimension_keys(merged_user, merged_group)

        gw_data = forward_upstream_data_or_none(
            await self._gw.update_template_mapping(normalized, mapping_id, updates)
        )
        if gw_data is None:
            return None

        updated = await self._repo.update(normalized, mapping_id, updates)
        if updated is None:
            if not isinstance(gw_data, dict):
                raise ValueError("invalid gateway response for template mapping")
            return _out_from_gateway(gw_data)
        return _row_to_out(updated)

    async def delete(self, jiuwenclaw_id: str, mapping_id: int) -> bool:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        gw_data = forward_upstream_data_or_none(
            await self._gw.delete_template_mapping(normalized, mapping_id)
        )
        if gw_data is None:
            return False
        return await self._repo.delete(normalized, mapping_id)
