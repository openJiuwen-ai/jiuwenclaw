"""配置生效全局兜底策略 config_effective_global_policy 业务逻辑。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.gateway_forward import (
    EnterpriseGatewayForward,
    forward_headers,
    forward_upstream_data,
    forward_upstream_data_or_none,
    normalize_list_page,
)
from jiuwenclaw_manager.core.instance.instance_service import get_instance_row
from jiuwenclaw_manager.infrastructure.utils import utc_now
from jiuwenclaw_manager.schemas.config_effective_policy_schemas import (
    ConfigEffectiveGlobalPolicyCreateBody,
    ConfigEffectiveGlobalPolicyOut,
    ConfigEffectiveGlobalPolicyUpdateBody,
)
from jiuwenclaw_manager.models.config_effective_policy_models import (
    CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF,
)

_GLOBAL_POLICY_TABLE = CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF.table_name


def _global_policy_pk(jiuwenclaw_id: str, policy_id: int) -> dict[str, Any]:
    return {"jiuwenclaw_id": jiuwenclaw_id, "id": policy_id}


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _normalize_channel_ids(value: list[str]) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("channel_ids must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def _row_to_out(row: Any) -> ConfigEffectiveGlobalPolicyOut:
    channel_ids = row.channel_ids
    if not isinstance(channel_ids, list):
        channel_ids = list(channel_ids) if channel_ids else []
    return ConfigEffectiveGlobalPolicyOut(
        id=row.id,
        jiuwenclaw_id=row.jiuwenclaw_id,
        default_model=row.default_model,
        video_model=row.video_model,
        audio_model=row.audio_model,
        vision_model=row.vision_model,
        channel_ids=channel_ids,
        enabled=row.enabled,
        data=row.data,
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
    )


def _out_from_gateway(data: dict[str, Any]) -> ConfigEffectiveGlobalPolicyOut:
    return ConfigEffectiveGlobalPolicyOut.model_validate(data)


class ConfigEffectiveGlobalPolicyService:
    def __init__(
        self,
        handler: DBHandler,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._handler = handler
        self._gw = EnterpriseGatewayForward(
            handler, extra_headers=extra_headers or forward_headers()
        )

    async def _validate_jiuwenclaw_id(self, jiuwenclaw_id: str) -> str:
        normalized = jiuwenclaw_id.strip()
        if not normalized:
            raise ValueError("jiuwenclaw_id is required")
        inst = await get_instance_row(self._handler, normalized)
        if inst is None:
            raise ValueError(f"unknown jiuwenclaw_id={normalized!r}")
        return normalized

    async def _get_by_jiuwenclaw_id(self, jiuwenclaw_id: str) -> Any | None:
        rows = await self._handler.list_records(
            _GLOBAL_POLICY_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id},
            limit=1,
            offset=0,
        )
        return rows[0] if rows else None

    async def _ensure_unique_jiuwenclaw_id(
        self, jiuwenclaw_id: str, *, exclude_policy_id: int | None = None
    ) -> None:
        existing = await self._get_by_jiuwenclaw_id(jiuwenclaw_id)
        if existing is None:
            return
        if exclude_policy_id is not None and existing.id == exclude_policy_id:
            return
        raise ValueError(
            f"global policy for jiuwenclaw_id={jiuwenclaw_id!r} already exists (id={existing.id})"
        )

    async def create(
        self, jiuwenclaw_id: str, body: ConfigEffectiveGlobalPolicyCreateBody
    ) -> ConfigEffectiveGlobalPolicyOut:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        await self._ensure_unique_jiuwenclaw_id(normalized)
        channel_ids = _normalize_channel_ids(body.channel_ids)

        gw_data = forward_upstream_data(
            await self._gw.create_global_policy(
                normalized, body.model_dump(mode="json")
            )
        )
        if not isinstance(gw_data, dict) or gw_data.get("id") is None:
            raise ValueError("gateway create global policy returned no id")

        now = utc_now()
        payload = {
            "id": int(gw_data["id"]),
            "jiuwenclaw_id": normalized,
            "default_model": body.default_model,
            "video_model": body.video_model,
            "audio_model": body.audio_model,
            "vision_model": body.vision_model,
            "channel_ids": channel_ids,
            "enabled": body.enabled,
            "data": body.data,
            "created_at": now,
            "updated_at": now,
        }
        created = await self._handler.create(_GLOBAL_POLICY_TABLE, payload)
        return _row_to_out(created)

    async def get(
        self, jiuwenclaw_id: str, policy_id: int
    ) -> ConfigEffectiveGlobalPolicyOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        gw_data = forward_upstream_data_or_none(
            await self._gw.get_global_policy(normalized, policy_id)
        )
        if gw_data is None:
            return None
        if not isinstance(gw_data, dict):
            raise ValueError("invalid gateway response for global policy")
        return _out_from_gateway(gw_data)

    async def list_policies(
        self,
        jiuwenclaw_id: str,
        *,
        page: int,
        page_size: int,
        enabled: bool | None,
    ) -> dict[str, Any]:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        gw_data = forward_upstream_data(
            await self._gw.list_global_policies(
                normalized,
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
        policy_id: int,
        body: ConfigEffectiveGlobalPolicyUpdateBody,
    ) -> ConfigEffectiveGlobalPolicyOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)

        updates = body.model_dump(exclude_unset=True)
        if "channel_ids" in updates and updates["channel_ids"] is not None:
            updates["channel_ids"] = _normalize_channel_ids(updates["channel_ids"])

        gw_data = forward_upstream_data_or_none(
            await self._gw.update_global_policy(normalized, policy_id, updates)
        )
        if gw_data is None:
            return None

        if updates:
            updates = dict(updates)
            updates["updated_at"] = utc_now()
            updated = await self._handler.update(
                _GLOBAL_POLICY_TABLE,
                _global_policy_pk(normalized, policy_id),
                updates,
            )
        else:
            updated = await self._handler.get(
                _GLOBAL_POLICY_TABLE, _global_policy_pk(normalized, policy_id)
            )
        if updated is None:
            if not isinstance(gw_data, dict):
                raise ValueError("invalid gateway response for global policy")
            return _out_from_gateway(gw_data)
        return _row_to_out(updated)

    async def delete(self, jiuwenclaw_id: str, policy_id: int) -> bool:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        gw_data = forward_upstream_data_or_none(
            await self._gw.delete_global_policy(normalized, policy_id)
        )
        if gw_data is None:
            return False
        return await self._handler.delete(
            _GLOBAL_POLICY_TABLE, _global_policy_pk(normalized, policy_id)
        )
