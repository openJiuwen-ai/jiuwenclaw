"""配置生效 Service 层级策略 config_effective_service_policy 业务逻辑。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.instance.instance_service import get_instance_row
from jiuwenclaw_manager.infrastructure.utils import utc_now
from jiuwenclaw_manager.manager_ws_server import ManagerWsServer
from jiuwenclaw_manager.manager_ws_server.server import push_to_instance
from jiuwenclaw_manager.models.config_effective_policy_models import (
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from jiuwenclaw_manager.core.config_effective_policy.template_ref import (
    apply_template_ref_to_updates,
    normalize_template_ref,
    read_template_ref_from_row,
)
from jiuwenclaw_manager.schemas.config_effective_policy_schemas import (
    ConfigEffectiveServicePolicyCreateBody,
    ConfigEffectiveServicePolicyOut,
    ConfigEffectiveServicePolicyUpdateBody,
)

_SERVICE_POLICY_TABLE = CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name


async def push_config_effective_service_policy_op(
    jiuwenclaw_id: str,
    op: str,
    *,
    policy: dict[str, Any] | None = None,
    policy_id: int | None = None,
    updates: dict[str, Any] | None = None,
    server: ManagerWsServer | None = None,
) -> dict[str, Any]:
    """推送 Service 层级配置生效策略变更（``config.config_effective_service_policies``），返回 config.ack payload。"""
    payload: dict[str, Any] = {
        "op": op,
        "jiuwenclaw_id": jiuwenclaw_id,
    }
    if policy is not None:
        payload["policy"] = policy
    if policy_id is not None:
        payload["policy_id"] = policy_id
    if updates is not None:
        payload["updates"] = updates
    return await push_to_instance(
        jiuwenclaw_id,
        config={"config_effective_service_policies": payload},
        server=server,
    )


def _service_policy_pk(jiuwenclaw_id: str, policy_id: int) -> dict[str, Any]:
    return {"jiuwenclaw_id": jiuwenclaw_id, "id": policy_id}


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
        template_ref=read_template_ref_from_row(row),
        enabled=row.enabled,
        data=row.data,
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
    )


class ConfigEffectiveServicePolicyService:
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

    def _policy_dict_for_push(self, row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        """构建经 WebSocket 下发给 Gateway 的 policy 对象（不含 id，由 Gateway 自增）。"""
        return {
            "jiuwenclaw_id": row["jiuwenclaw_id"],
            "service_id": row["service_id"],
            "priority": row["priority"],
            "match_expr": row.get("match_expr"),
            "template_ref": row.get("template_ref") or {},
            "enabled": row.get("enabled", True),
            "data": row.get("data"),
            "created_at": _iso(row.get("created_at") or now),
            "updated_at": _iso(row.get("updated_at") or now),
        }

    async def create(
        self,
        jiuwenclaw_id: str,
        body: ConfigEffectiveServicePolicyCreateBody,
        *,
        ws_server: ManagerWsServer | None = None,
    ) -> ConfigEffectiveServicePolicyOut:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)

        now = utc_now()
        row = {
            "service_id": body.service_id.strip(),
            "jiuwenclaw_id": normalized,
            "priority": body.priority,
            "match_expr": body.match_expr,
            "template_ref": normalize_template_ref(body.template_ref),
            "enabled": body.enabled,
            "data": body.data,
            "created_at": now,
            "updated_at": now,
        }
        ack = await push_config_effective_service_policy_op(
            normalized,
            "create",
            policy=self._policy_dict_for_push(row, now=now),
            server=ws_server,
        )
        ack_result = ack.get("result") if isinstance(ack, dict) else None
        policy_id: int | None = None
        if isinstance(ack_result, dict):
            raw_id = ack_result.get("policy_id")
            if raw_id is not None:
                policy_id = int(raw_id)
        if policy_id is None or policy_id < 1:
            raise ValueError(
                "gateway config_effective_service_policies.create returned no policy_id"
            )

        payload = {**row, "id": policy_id}
        created = await self._handler.create(_SERVICE_POLICY_TABLE, payload)
        return _row_to_out(created)

    async def get(
        self, jiuwenclaw_id: str, policy_id: int
    ) -> ConfigEffectiveServicePolicyOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        row = await self._handler.get(
            _SERVICE_POLICY_TABLE, _service_policy_pk(normalized, policy_id)
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
        enabled: bool | None,
    ) -> dict[str, Any]:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        filters: dict[str, Any] = {"jiuwenclaw_id": normalized}
        if enabled is not None:
            filters["enabled"] = enabled

        offset = (page - 1) * page_size
        rows = await self._handler.list_records(
            _SERVICE_POLICY_TABLE, filters, limit=page_size, offset=offset
        )
        total = await self._handler.count_records(_SERVICE_POLICY_TABLE, filters)
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
        body: ConfigEffectiveServicePolicyUpdateBody,
        *,
        ws_server: ManagerWsServer | None = None,
    ) -> ConfigEffectiveServicePolicyOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)

        updates = body.model_dump(exclude_unset=True)
        if "service_id" in updates and updates["service_id"] is not None:
            updates["service_id"] = updates["service_id"].strip()
            if not updates["service_id"]:
                raise ValueError("service_id cannot be empty")

        row = await self._handler.get(
            _SERVICE_POLICY_TABLE, _service_policy_pk(normalized, policy_id)
        )
        if row is None:
            return None

        if not updates:
            return _row_to_out(row)

        updates = apply_template_ref_to_updates(updates, existing_row=row)

        await push_config_effective_service_policy_op(
            normalized,
            "update",
            policy_id=policy_id,
            updates=updates,
            server=ws_server,
        )
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        updated = await self._handler.update(
            _SERVICE_POLICY_TABLE,
            _service_policy_pk(normalized, policy_id),
            payload,
        )
        if updated is None:
            return None
        return _row_to_out(updated)

    async def delete(
        self,
        jiuwenclaw_id: str,
        policy_id: int,
        *,
        ws_server: ManagerWsServer | None = None,
    ) -> bool:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        row = await self._handler.get(
            _SERVICE_POLICY_TABLE, _service_policy_pk(normalized, policy_id)
        )
        if row is None:
            return False
        await push_config_effective_service_policy_op(
            normalized,
            "delete",
            policy_id=policy_id,
            server=ws_server,
        )
        return await self._handler.delete(
            _SERVICE_POLICY_TABLE, _service_policy_pk(normalized, policy_id)
        )
