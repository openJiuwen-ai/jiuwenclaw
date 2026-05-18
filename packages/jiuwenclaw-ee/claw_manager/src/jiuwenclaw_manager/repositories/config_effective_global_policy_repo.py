"""数据访问层：config_effective_global_policy（DBHandler）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.utils import utc_now
from jiuwenclaw_manager.models.table_defs.enterprise_models import (
    CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF,
)

_TABLE = CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF.table_name


def _pk(jiuwenclaw_id: str, policy_id: int) -> dict[str, Any]:
    return {"jiuwenclaw_id": jiuwenclaw_id, "id": policy_id}


class ConfigEffectiveGlobalPolicyRepository:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def create(self, row_data: dict[str, Any]) -> Any:
        now = utc_now()
        payload = dict(row_data)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        return await self._handler.create(_TABLE, payload)

    async def get(self, jiuwenclaw_id: str, policy_id: int) -> Any | None:
        return await self._handler.get(_TABLE, _pk(jiuwenclaw_id, policy_id))

    async def get_by_jiuwenclaw_id(self, jiuwenclaw_id: str) -> Any | None:
        rows = await self._handler.list_records(
            _TABLE, {"jiuwenclaw_id": jiuwenclaw_id}, limit=1, offset=0
        )
        return rows[0] if rows else None

    async def list(
        self,
        *,
        jiuwenclaw_id: str | None,
        enabled: bool | None,
        offset: int,
        limit: int,
    ) -> tuple[Sequence[Any], int]:
        filters: dict[str, Any] = {}
        if jiuwenclaw_id:
            filters["jiuwenclaw_id"] = jiuwenclaw_id
        if enabled is not None:
            filters["enabled"] = enabled
        total = await self._handler.count_records(_TABLE, filters)
        rows = await self._handler.list_records(
            _TABLE, filters, limit=limit, offset=offset
        )
        return rows, int(total)

    async def update(
        self, jiuwenclaw_id: str, policy_id: int, updates: dict[str, Any]
    ) -> Any | None:
        if not updates:
            return await self.get(jiuwenclaw_id, policy_id)
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        return await self._handler.update(_TABLE, _pk(jiuwenclaw_id, policy_id), payload)

    async def delete(self, jiuwenclaw_id: str, policy_id: int) -> bool:
        return await self._handler.delete(_TABLE, _pk(jiuwenclaw_id, policy_id))
