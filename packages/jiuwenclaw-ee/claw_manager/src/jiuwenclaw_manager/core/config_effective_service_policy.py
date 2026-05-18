"""???? Service ???? config_effective_service_policy ?????"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.models.schemas import (
    ConfigEffectiveServicePolicyCreateBody,
    ConfigEffectiveServicePolicyOut,
    ConfigEffectiveServicePolicyUpdateBody,
)
from jiuwenclaw_manager.repositories.config_effective_service_policy_repo import (
    ConfigEffectiveServicePolicyRepository,
)
from jiuwenclaw_manager.repositories.instance_repo import InstanceRepository
from jiuwenclaw_manager.core.gateway_forward import (
    EnterpriseGatewayForward,
    forward_headers,
    forward_upstream_data,
    forward_upstream_data_or_none,
    normalize_list_page,
)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _row_to_out(row: Any) -> ConfigEffectiveServicePolicyOut:
    return ConfigEffectiveServicePolicyOut(
        id=row.id,
        service_id=row.service_id,
        jiuwenclaw_id=row.jiuwenclaw_id,
        priority=row.priority,
        match_expr=row.match_expr,
        default_model=row.default_model,
        video_model=row.video_model,
        audio_model=row.audio_model,
        vision_model=row.vision_model,
        enabled=row.enabled,
        data=row.data,
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
    )


def _out_from_gateway(data: dict[str, Any]) -> ConfigEffectiveServicePolicyOut:
    return ConfigEffectiveServicePolicyOut.model_validate(data)


class ConfigEffectiveServicePolicyService:
    def __init__(
        self,
        handler: DBHandler,
        http_client: httpx.AsyncClient,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._handler = handler
        self._repo = ConfigEffectiveServicePolicyRepository(handler)
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
        self, jiuwenclaw_id: str, body: ConfigEffectiveServicePolicyCreateBody
    ) -> ConfigEffectiveServicePolicyOut:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        gw_data = forward_upstream_data(
            await self._gw.create_service_policy(
                normalized, body.model_dump(mode="json")
            )
        )
        if not isinstance(gw_data, dict) or gw_data.get("id") is None:
            raise ValueError("gateway create service policy returned no id")

        created = await self._repo.create(
            {
                "id": int(gw_data["id"]),
                "service_id": body.service_id.strip(),
                "jiuwenclaw_id": normalized,
                "priority": body.priority,
                "match_expr": body.match_expr,
                "default_model": body.default_model,
                "video_model": body.video_model,
                "audio_model": body.audio_model,
                "vision_model": body.vision_model,
                "enabled": body.enabled,
                "data": body.data,
            }
        )
        return _row_to_out(created)

    async def get(
        self, jiuwenclaw_id: str, policy_id: int
    ) -> ConfigEffectiveServicePolicyOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        gw_data = forward_upstream_data_or_none(
            await self._gw.get_service_policy(normalized, policy_id)
        )
        if gw_data is None:
            return None
        if not isinstance(gw_data, dict):
            raise ValueError("invalid gateway response for service policy")
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
            await self._gw.list_service_policies(
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
        body: ConfigEffectiveServicePolicyUpdateBody,
    ) -> ConfigEffectiveServicePolicyOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)

        updates = body.model_dump(exclude_unset=True)
        if "service_id" in updates and updates["service_id"] is not None:
            updates["service_id"] = updates["service_id"].strip()

        gw_data = forward_upstream_data_or_none(
            await self._gw.update_service_policy(normalized, policy_id, updates)
        )
        if gw_data is None:
            return None

        updated = await self._repo.update(normalized, policy_id, updates)
        if updated is None:
            if not isinstance(gw_data, dict):
                raise ValueError("invalid gateway response for service policy")
            return _out_from_gateway(gw_data)
        return _row_to_out(updated)

    async def delete(self, jiuwenclaw_id: str, policy_id: int) -> bool:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        gw_data = forward_upstream_data_or_none(
            await self._gw.delete_service_policy(normalized, policy_id)
        )
        if gw_data is None:
            return False
        await self._repo.delete(normalized, policy_id)
        return True
