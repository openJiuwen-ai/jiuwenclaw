"""配置生效 Agent 层级策略 config_effective_agent_policy 业务逻辑。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.template.push_template_to_gateway import (
    sync_gateway_templates_after_template_ref_change,
)
from jiuwenclaw_manager.infrastructure.common import (
    DEFAULT_POLICY_ORDER_BY,
    resolve_order_by,
)
from jiuwenclaw_manager.infrastructure.jiuwenclaw_id import validate_jiuwenclaw_id
from jiuwenclaw_manager.infrastructure.utils import (
    iso_datetime,
    new_uuid4,
    utc_now,
)
from jiuwenclaw_manager.manager_ws_server.server import push_config_op
from jiuwenclaw_manager.models.config_effective_policy_models import (
    CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from jiuwenclaw_manager.infrastructure.template_ref import (
    apply_template_ref_to_updates,
    normalize_template_ref,
    read_template_ref_from_row,
)
from jiuwenclaw_manager.schemas.config_effective_policy_schemas import (
    ConfigEffectiveAgentPolicyCreateBody,
    ConfigEffectiveAgentPolicyListQuery,
    ConfigEffectiveAgentPolicyOut,
    ConfigEffectiveAgentPolicyUpdateBody,
)

_AGENT_POLICY_TABLE = CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF.table_name
_SERVICE_POLICY_TABLE = CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name
_LIST_ALL_CAP = 10_000
_ALLOWED_SORT_FIELDS = frozenset({
    "policy_name",
    "policy_desc",
    "service_policy_id",
    "priority",
    "match_expr",
    "agent_id",
    "updated_at",
})


def _matches_search(
    row: Any,
    query: str,
    *,
    service_policy_names: dict[str, str],
) -> bool:
    needle = query.strip().lower()
    if not needle:
        return True
    fields = [
        str(getattr(row, "policy_id", "") or ""),
        str(getattr(row, "policy_name", "") or ""),
        str(getattr(row, "policy_desc", "") or ""),
        str(getattr(row, "agent_id", "") or ""),
        str(getattr(row, "service_policy_id", "") or ""),
        str(getattr(row, "priority", "") or ""),
        str(getattr(row, "match_expr", "") or ""),
    ]
    linked_service_name = service_policy_names.get(
        str(getattr(row, "service_policy_id", "") or ""),
        "",
    )
    if linked_service_name:
        fields.append(linked_service_name)
    return any(needle in field.lower() for field in fields)


async def _service_policy_names_by_id(handler: DBHandler, jiuwenclaw_id: str) -> dict[str, str]:
    rows = await handler.list_records(
        _SERVICE_POLICY_TABLE,
        {"jiuwenclaw_id": jiuwenclaw_id},
        limit=_LIST_ALL_CAP,
        offset=0,
    )
    return {
        str(getattr(row, "policy_id", "") or ""): str(getattr(row, "policy_name", "") or "")
        for row in rows
    }


async def push_config_effective_agent_policy_op(
    jiuwenclaw_id: str,
    op: str,
    *,
    policy: dict[str, Any] | None = None,
    row_id: int | None = None,
    updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """推送 Agent 层级配置生效策略变更（``config.config_effective_agent_policies``），返回 config.ack payload。"""
    payload: dict[str, Any] = {"op": op}
    if policy is not None:
        payload["policy"] = policy
    if row_id is not None:
        payload["id"] = row_id
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
        jiuwenclaw_id=row.jiuwenclaw_id,
        policy_id=row.policy_id,
        policy_name=row.policy_name,
        policy_desc=row.policy_desc,
        agent_id=row.agent_id,
        service_policy_id=row.service_policy_id,
        priority=row.priority,
        match_expr=row.match_expr,
        template_ref=read_template_ref_from_row(row),
        send_file_allowed=bool(getattr(row, "send_file_allowed", False)),
        enabled=row.enabled,
        data=row.data,
        created_at=iso_datetime(row.created_at),
        updated_at=iso_datetime(row.updated_at),
    )


class ConfigEffectiveAgentPolicyService:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def _validate_parent_refs(
        self,
        *,
        jiuwenclaw_id: str,
        service_policy_id: str,
    ) -> None:
        normalized_id = service_policy_id.strip()
        if not normalized_id:
            raise ValueError("service_policy_id is required")
        sp = await self._handler.get(
            _SERVICE_POLICY_TABLE,
            {"jiuwenclaw_id": jiuwenclaw_id.strip(), "policy_id": normalized_id},
        )
        if sp is None:
            raise ValueError(f"unknown service_policy_id={normalized_id!r}")

    def _policy_dict_for_push(self, row: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        """构建经 WebSocket 下发给 Gateway 的 policy 对象（不含 id，由 Gateway 自增）。"""
        return {
            "jiuwenclaw_id": row["jiuwenclaw_id"],
            "policy_id": row["policy_id"],
            "policy_name": row.get("policy_name"),
            "policy_desc": row.get("policy_desc"),
            "agent_id": row["agent_id"],
            "service_policy_id": row["service_policy_id"],
            "priority": row.get("priority", 0),
            "match_expr": row.get("match_expr"),
            "template_ref": normalize_template_ref(row.get("template_ref")),
            "send_file_allowed": bool(row.get("send_file_allowed", False)),
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
        normalized = await validate_jiuwenclaw_id(self._handler, jiuwenclaw_id)
        await self._validate_parent_refs(
            jiuwenclaw_id=normalized,
            service_policy_id=body.service_policy_id,
        )

        now = utc_now()
        row = {
            "jiuwenclaw_id": normalized,
            "policy_id": new_uuid4(),
            "policy_name": body.policy_name,
            "policy_desc": body.policy_desc,
            "agent_id": body.agent_id.strip(),
            "service_policy_id": body.service_policy_id.strip(),
            "priority": body.priority,
            "match_expr": body.match_expr,
            "template_ref": normalize_template_ref(body.template_ref),
            "send_file_allowed": body.send_file_allowed,
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
        row_id: int | None = None
        if isinstance(ack_result, dict):
            raw_id = ack_result.get("id")
            if raw_id is not None:
                row_id = int(raw_id)
        if row_id is None or row_id < 1:
            raise ValueError(
                "gateway config_effective_agent_policies.create returned no id"
            )

        payload = {**row, "id": row_id}
        created = await self._handler.create(_AGENT_POLICY_TABLE, payload)
        await sync_gateway_templates_after_template_ref_change(
            self._handler,
            normalized,
            old_template_ref={},
            new_template_ref=row["template_ref"],
        )
        return _row_to_out(created)

    async def get(
        self, jiuwenclaw_id: str, policy_id: int
    ) -> ConfigEffectiveAgentPolicyOut | None:
        normalized = await validate_jiuwenclaw_id(self._handler, jiuwenclaw_id)
        row = await self._handler.get(
            _AGENT_POLICY_TABLE, _agent_policy_pk(normalized, policy_id)
        )
        if row is None:
            return None
        return _row_to_out(row)

    async def list_policies(
        self,
        jiuwenclaw_id: str,
        query: ConfigEffectiveAgentPolicyListQuery,
    ) -> dict[str, Any]:
        normalized = await validate_jiuwenclaw_id(self._handler, jiuwenclaw_id)
        page = max(query.page, 1)
        page_size = min(max(query.page_size, 1), 200)
        filters: dict[str, Any] = {"jiuwenclaw_id": normalized}
        if query.service_policy_id is not None:
            filters["service_policy_id"] = query.service_policy_id.strip()
        if query.enabled is not None:
            filters["enabled"] = query.enabled
        if query.send_file_allowed is not None:
            filters["send_file_allowed"] = query.send_file_allowed

        order_by = resolve_order_by(
            query.sort_by,
            query.sort_order,
            allowed_sort_fields=_ALLOWED_SORT_FIELDS,
            default_order_by=DEFAULT_POLICY_ORDER_BY,
        )
        search_query = (query.search or "").strip()

        service_policy_names: dict[str, str] = {}
        if search_query:
            service_policy_names = await _service_policy_names_by_id(
                self._handler, normalized
            )

        if search_query:
            rows = await self._handler.list_records(
                _AGENT_POLICY_TABLE,
                filters,
                limit=_LIST_ALL_CAP,
                offset=0,
                order_by=order_by,
            )
            matched_rows = []
            for row in rows:
                if _matches_search(
                    row,
                    search_query,
                    service_policy_names=service_policy_names,
                ):
                    matched_rows.append(row)
            rows = matched_rows
            total = len(rows)
            offset = (page - 1) * page_size
            page_rows = rows[offset:offset + page_size]
            items = [_row_to_out(row).model_dump(mode="json") for row in page_rows]
            return {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
            }

        offset = (page - 1) * page_size
        rows = await self._handler.list_records(
            _AGENT_POLICY_TABLE,
            filters,
            limit=page_size,
            offset=offset,
            order_by=order_by,
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
        normalized = await validate_jiuwenclaw_id(self._handler, jiuwenclaw_id)

        updates = body.model_dump(exclude_unset=True)
        row = await self._handler.get(
            _AGENT_POLICY_TABLE, _agent_policy_pk(normalized, policy_id)
        )
        if row is None:
            return None

        next_jiuwenclaw_id = row.jiuwenclaw_id
        if "agent_id" in updates and updates["agent_id"] is not None:
            updates["agent_id"] = updates["agent_id"].strip()
            if not updates["agent_id"]:
                raise ValueError("agent_id cannot be empty")

        if "service_policy_id" in updates and updates["service_policy_id"] is not None:
            updates["service_policy_id"] = str(updates["service_policy_id"]).strip()
            if not updates["service_policy_id"]:
                raise ValueError("service_policy_id cannot be empty")

        next_service_policy_id = updates.get("service_policy_id", row.service_policy_id)
        if "service_policy_id" in updates:
            await self._validate_parent_refs(
                jiuwenclaw_id=next_jiuwenclaw_id,
                service_policy_id=next_service_policy_id,
            )

        if not updates:
            return _row_to_out(row)

        old_template_ref = read_template_ref_from_row(row)
        updates = apply_template_ref_to_updates(updates, existing_row=row)

        await push_config_effective_agent_policy_op(
            normalized,
            "update",
            row_id=policy_id,
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
        if "template_ref" in updates:
            await sync_gateway_templates_after_template_ref_change(
                self._handler,
                normalized,
                old_template_ref=old_template_ref,
                new_template_ref=updates["template_ref"],
            )
        return _row_to_out(updated)

    async def delete(
        self,
        jiuwenclaw_id: str,
        policy_id: int,
    ) -> bool:
        normalized = await validate_jiuwenclaw_id(self._handler, jiuwenclaw_id)
        row = await self._handler.get(
            _AGENT_POLICY_TABLE, _agent_policy_pk(normalized, policy_id)
        )
        if row is None:
            return False
        await push_config_effective_agent_policy_op(
            normalized,
            "delete",
            row_id=policy_id,
        )
        deleted = await self._handler.delete(
            _AGENT_POLICY_TABLE, _agent_policy_pk(normalized, policy_id)
        )
        if deleted:
            await sync_gateway_templates_after_template_ref_change(
                self._handler,
                normalized,
                old_template_ref=read_template_ref_from_row(row),
                new_template_ref={},
            )
        return deleted
