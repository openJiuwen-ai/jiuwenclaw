"""配置生效 Agent 层级策略 config_effective_agent_policy 业务逻辑。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.models.schemas import (
    ConfigEffectiveAgentPolicyCreateBody,
    ConfigEffectiveAgentPolicyOut,
    ConfigEffectiveAgentPolicyUpdateBody,
)
from jiuwenclaw_manager.repositories.config_effective_agent_policy_repo import (
    ConfigEffectiveAgentPolicyRepository,
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


def _row_to_out(row: Any) -> ConfigEffectiveAgentPolicyOut:
    return ConfigEffectiveAgentPolicyOut(
        id=row.id,
        agent_id=row.agent_id,
        jiuwenclaw_id=row.jiuwenclaw_id,
        service_policy_id=row.service_policy_id,
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


def _out_from_gateway(data: dict[str, Any]) -> ConfigEffectiveAgentPolicyOut:
    return ConfigEffectiveAgentPolicyOut.model_validate(data)


class ConfigEffectiveAgentPolicyService:
    def __init__(
        self,
        handler: DBHandler,
        http_client: httpx.AsyncClient,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._handler = handler
        self._repo = ConfigEffectiveAgentPolicyRepository(handler)
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

    async def _validate_parent_refs(
        self,
        *,
        jiuwenclaw_id: str,
        service_policy_id: int,
    ) -> None:
        inst = await self._instance_repo.get(jiuwenclaw_id.strip())
        if inst is None:
            raise ValueError(f"unknown jiuwenclaw_id={jiuwenclaw_id!r}")

        sp = await self._repo.get_service_policy(
            jiuwenclaw_id.strip(), service_policy_id
        )
        if sp is None:
            raise ValueError(f"unknown service_policy_id={service_policy_id}")

    async def create(
        self, jiuwenclaw_id: str, body: ConfigEffectiveAgentPolicyCreateBody
    ) -> ConfigEffectiveAgentPolicyOut:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        await self._validate_parent_refs(
            jiuwenclaw_id=normalized,
            service_policy_id=body.service_policy_id,
        )

        gw_data = forward_upstream_data(
            await self._gw.create_agent_policy(
                normalized, body.model_dump(mode="json")
            )
        )
        if not isinstance(gw_data, dict) or gw_data.get("id") is None:
            raise ValueError("gateway create agent policy returned no id")

        created = await self._repo.create(
            {
                "id": int(gw_data["id"]),
                "agent_id": body.agent_id.strip(),
                "jiuwenclaw_id": normalized,
                "service_policy_id": body.service_policy_id,
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
    ) -> ConfigEffectiveAgentPolicyOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        gw_data = forward_upstream_data_or_none(
            await self._gw.get_agent_policy(normalized, policy_id)
        )
        if gw_data is None:
            return None
        if not isinstance(gw_data, dict):
            raise ValueError("invalid gateway response for agent policy")
        return _out_from_gateway(gw_data)

    async def list_policies(
        self,
        jiuwenclaw_id: str,
        *,
        page: int,
        page_size: int,
        service_policy_id: int | None,
        enabled: bool | None,
    ) -> dict[str, Any]:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        gw_data = forward_upstream_data(
            await self._gw.list_agent_policies(
                normalized,
                service_policy_id=service_policy_id,
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
        body: ConfigEffectiveAgentPolicyUpdateBody,
    ) -> ConfigEffectiveAgentPolicyOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)

        updates = body.model_dump(exclude_unset=True)
        row = await self._repo.get(normalized, policy_id)
        next_jiuwenclaw_id = row.jiuwenclaw_id if row else normalized
        next_service_policy_id = updates.get(
            "service_policy_id", row.service_policy_id if row else None
        )
        if "agent_id" in updates and updates["agent_id"] is not None:
            updates["agent_id"] = updates["agent_id"].strip()

        if "service_policy_id" in updates and next_service_policy_id is not None:
            await self._validate_parent_refs(
                jiuwenclaw_id=next_jiuwenclaw_id,
                service_policy_id=next_service_policy_id,
            )

        gw_data = forward_upstream_data_or_none(
            await self._gw.update_agent_policy(normalized, policy_id, updates)
        )
        if gw_data is None:
            return None

        updated = await self._repo.update(normalized, policy_id, updates)
        if updated is None:
            if not isinstance(gw_data, dict):
                raise ValueError("invalid gateway response for agent policy")
            return _out_from_gateway(gw_data)
        return _row_to_out(updated)

    async def delete(self, jiuwenclaw_id: str, policy_id: int) -> bool:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        gw_data = forward_upstream_data_or_none(
            await self._gw.delete_agent_policy(normalized, policy_id)
        )
        if gw_data is None:
            return False
        await self._repo.delete(normalized, policy_id)
        return True
