"""数据访问层：config_default_template_mapping（DBHandler）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.utils import utc_now
from jiuwenclaw_manager.models.table_defs.enterprise_models import (
    CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF,
)

_TABLE = CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF.table_name


def _pk(jiuwenclaw_id: str, mapping_id: int) -> dict[str, Any]:
    return {"jiuwenclaw_id": jiuwenclaw_id, "id": mapping_id}


class ConfigDefaultTemplateMappingRepository:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def create(self, row_data: dict[str, Any]) -> Any:
        now = utc_now()
        payload = dict(row_data)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        return await self._handler.create(_TABLE, payload)

    async def get(self, jiuwenclaw_id: str, mapping_id: int) -> Any | None:
        return await self._handler.get(_TABLE, _pk(jiuwenclaw_id, mapping_id))

    async def list(
        self,
        *,
        jiuwenclaw_id: str | None,
        user_id: str | None,
        group_id: str | None,
        template_type: str | None,
        template_id: str | None,
        enabled: bool | None,
        offset: int,
        limit: int,
    ) -> tuple[Sequence[Any], int]:
        filters: dict[str, Any] = {}
        if jiuwenclaw_id:
            filters["jiuwenclaw_id"] = jiuwenclaw_id
        if user_id:
            filters["user_id"] = user_id
        if group_id:
            filters["group_id"] = group_id
        if template_type:
            filters["template_type"] = template_type
        if template_id:
            filters["template_id"] = template_id
        if enabled is not None:
            filters["enabled"] = enabled
        total = await self._handler.count_records(_TABLE, filters)
        rows = await self._handler.list_records(
            _TABLE, filters, limit=limit, offset=offset
        )
        return rows, int(total)

    async def update(
        self, jiuwenclaw_id: str, mapping_id: int, updates: dict[str, Any]
    ) -> Any | None:
        if not updates:
            return await self.get(jiuwenclaw_id, mapping_id)
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        return await self._handler.update(_TABLE, _pk(jiuwenclaw_id, mapping_id), payload)

    async def delete(self, jiuwenclaw_id: str, mapping_id: int) -> bool:
        return await self._handler.delete(_TABLE, _pk(jiuwenclaw_id, mapping_id))
