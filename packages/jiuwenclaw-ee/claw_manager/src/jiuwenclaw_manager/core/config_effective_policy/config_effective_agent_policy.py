"""配置生效 Agent 层级策略 config_effective_agent_policy 业务逻辑。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.instance.instance_service import get_instance_row
from jiuwenclaw_manager.infrastructure.utils import iso_datetime, utc_now
from jiuwenclaw_manager.manager_ws_server.server import push_config_op
from jiuwenclaw_manager.models.config_effective_policy_models import (
    CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from jiuwenclaw_manager.core.config_effective_policy.template_ref import (
    apply_template_ref_to_updates,
    normalize_template_ref,
    read_template_ref_from_row,
)
from jiuwenclaw_manager.schemas.config_effective_policy_schemas import (
    ConfigEffectiveAgentPolicyCreateBody,
    ConfigEffectiveAgentPolicyOut,
    ConfigEffectiveAgentPolicyUpdateBody,
)

_AGENT_POLICY_TABLE = CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF.table_name
_SERVICE_POLICY_TABLE = CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name


async def push_config_effective_agent_policy_op(
    jiuwenclaw_id: str,
    op: str,
    *,
    policy: dict[str, Any] | None = None,
    policy_id: int | None = None,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """推送 Agent 层级配置生效策略变更（``config.config_effective_agent_policies``），返回 config.ack payload。"""
    payload: dict[str, Any] = {"op": op}
    if policy is not None:
        payload["policy"] = policy
    if policy_id is not None:
        payload["policy_id"] = policy_id
    if updates is not None:
        payload["updates"] = updates
    return await push_config_op(
        jiuwenclaw_id,
        {"config_effective_agent_policies": payload},
    )


def _agent_policy_pk(jiuwenclaw_id: str, policy_id: int) -> dict[str, Any]:
    return {"jiuwenclaw_id": jiuwenclaw_id, "id": policy_id}


def _row_to_out(row: Any) -> ConfigEffectiveAgentPolicyOut:
    return ConfigEffectiveAgentPolicyOut(
        id=row.id,
        agent_id=row.agent_id,
        jiuwenclaw_id=row.jiuwenclaw_id,
        service_policy_id=row.service_policy_id,
        priority=row.priority,
        match_expr=row.match_expr,
        template_ref=read_template_ref_from_row(row),
        enabled=row.enabled,
        data=row.data,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class ConfigEffectiveAgentPolicyService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def _validate_jiuwenclaw_id(self, jiuwenclaw_id: str) -> str:
        normalized = jiuwenclaw_id.strip()
        if not normalized:
            raise ValueError("jiuwenclaw_id is required")
        inst = await get_instance_row(self._handler, normalized)
        if inst is None:
            raise ValueError(f"unknown jiuwenclaw_id={normalized!r}")
        return normalized

    async def _validate_parent_refs(
        self,
        *,
        jiuwenclaw_id: str,
        service_policy_id: int,
    ) -> None:
        sp = await self._handler.get(
            _SERVICE_POLICY_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id.strip(), "id": service_policy_id},
        )
        if sp is None:
            raise ValueError(f"unknown service_policy_id={service_policy_id}")

    def _policy_dict_for_push(self, row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        """构建经 WebSocket 下发给 Gateway 的 policy 对象（不含 id，由 Gateway 自增）。"""
        return {
            "jiuwenclaw_id": row["jiuwenclaw_id"],
            "agent_id": row["agent_id"],
            "service_policy_id": row["service_policy_id"],
            "priority": row.get("priority", 0),
            "match_expr": row.get("match_expr"),
            "template_ref": normalize_template_ref(row.get("template_ref")),
            "enabled": row.get("enabled", True),
            "data": row.get("data"),
            "created_at": iso_datetime(row.get("created_at") or now),
            "updated_at": iso_datetime(row.get("updated_at") or now),
        }

    async def create(
        self,
        jiuwenclaw_id: str,
        body: ConfigEffectiveAgentPolicyCreateBody,
    ) -> ConfigEffectiveAgentPolicyOut:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        await self._validate_parent_refs(
            jiuwenclaw_id=normalized,
            service_policy_id=body.service_policy_id,
        )

        now = utc_now()
        row = {
            "agent_id": body.agent_id.strip(),
            "jiuwenclaw_id": normalized,
            "service_policy_id": body.service_policy_id,
            "priority": body.priority,
            "match_expr": body.match_expr,
            "template_ref": normalize_template_ref(body.template_ref),
            "enabled": body.enabled,
            "data": body.data,
            "created_at": now,
            "updated_at": now,
        }
        ack = await push_config_effective_agent_policy_op(
            normalized,
            "create",
            policy=self._policy_dict_for_push(row, now=now),
        )
        ack_result = ack.get("result") if isinstance(ack, dict) else None
        policy_id: int | None = None
        if isinstance(ack_result, dict):
            raw_id = ack_result.get("policy_id")
            if raw_id is not None:
                policy_id = int(raw_id)
        if policy_id is None or policy_id < 1:
            raise ValueError(
                "gateway config_effective_agent_policies.create returned no policy_id"
            )

        payload = {**row, "id": policy_id}
        created = await self._handler.create(_AGENT_POLICY_TABLE, payload)
        return _row_to_out(created)

    async def get(
        self, jiuwenclaw_id: str, policy_id: int
    ) -> ConfigEffectiveAgentPolicyOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        row = await self._handler.get(
            _AGENT_POLICY_TABLE, _agent_policy_pk(normalized, policy_id)
        )
        if row is None:
            return None
        return _row_to_out(row)

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
        filters: dict[str, Any] = {"jiuwenclaw_id": normalized}
        if service_policy_id is not None:
            filters["service_policy_id"] = service_policy_id
        if enabled is not None:
            filters["enabled"] = enabled

        offset = (page - 1) * page_size
        rows = await self._handler.list_records(
            _AGENT_POLICY_TABLE, filters, limit=page_size, offset=offset
        )
        total = await self._handler.count_records(_AGENT_POLICY_TABLE, filters)
        items = [_row_to_out(r).model_dump(mode="json") for r in rows]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def update(
        self,
        jiuwenclaw_id: str,
        policy_id: int,
        body: ConfigEffectiveAgentPolicyUpdateBody,
    ) -> ConfigEffectiveAgentPolicyOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)

        updates = body.model_dump(exclude_unset=True)
        row = await self._handler.get(
            _AGENT_POLICY_TABLE, _agent_policy_pk(normalized, policy_id)
        )
        if row is None:
            return None

        next_jiuwenclaw_id = row.jiuwenclaw_id
        next_service_policy_id = updates.get("service_policy_id", row.service_policy_id)
        if "agent_id" in updates and updates["agent_id"] is not None:
            updates["agent_id"] = updates["agent_id"].strip()
            if not updates["agent_id"]:
                raise ValueError("agent_id cannot be empty")

        if "service_policy_id" in updates and next_service_policy_id is not None:
            await self._validate_parent_refs(
                jiuwenclaw_id=next_jiuwenclaw_id,
                service_policy_id=next_service_policy_id,
            )

        if not updates:
            return _row_to_out(row)

        updates = apply_template_ref_to_updates(updates, existing_row=row)

        await push_config_effective_agent_policy_op(
            normalized,
            "update",
            policy_id=policy_id,
            updates=updates,
        )
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        updated = await self._handler.update(
            _AGENT_POLICY_TABLE,
            _agent_policy_pk(normalized, policy_id),
            payload,
        )
        if updated is None:
            return None
        return _row_to_out(updated)

    async def delete(
        self,
        jiuwenclaw_id: str,
        policy_id: int,
    ) -> bool:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        row = await self._handler.get(
            _AGENT_POLICY_TABLE, _agent_policy_pk(normalized, policy_id)
        )
        if row is None:
            return False
        await push_config_effective_agent_policy_op(
            normalized,
            "delete",
            policy_id=policy_id,
        )
        return await self._handler.delete(
            _AGENT_POLICY_TABLE, _agent_policy_pk(normalized, policy_id)
        )
