"""数据访问层：模型模板 model_template（DBHandler）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.utils import utc_now
from jiuwenclaw_manager.models.table_defs.enterprise_models import MODEL_TEMPLATE_TABLE_DEF

_TABLE = MODEL_TEMPLATE_TABLE_DEF.table_name


def _pk(jiuwenclaw_id: str, template_id: int) -> dict[str, Any]:
    return {"jiuwenclaw_id": jiuwenclaw_id, "id": template_id}


class ModelTemplateRepository:
    def __init__(self, handler: DBHandler) -> None:
        self._handler = handler

    async def create(self, row_data: dict[str, Any]) -> Any:
        now = utc_now()
        payload = dict(row_data)
        payload.setdefault("created_at", now)
        payload.setdefault("updated_at", now)
        return await self._handler.create(_TABLE, payload)

    async def get(self, jiuwenclaw_id: str, template_id: int) -> Any | None:
        return await self._handler.get(_TABLE, _pk(jiuwenclaw_id, template_id))

    async def list(
        self,
        *,
        jiuwenclaw_id: str | None,
        enabled: bool | None,
        model_type: str | None,
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
        if model_type:
            rows = [r for r in rows if _matches_model_type(getattr(r, "model_type", None), model_type)]
            total = len(rows)
        return rows, int(total)

    async def update(
        self, jiuwenclaw_id: str, template_id: int, updates: dict[str, Any]
    ) -> Any | None:
        if not updates:
            return await self.get(jiuwenclaw_id, template_id)
        payload = dict(updates)
        payload["updated_at"] = utc_now()
        return await self._handler.update(_TABLE, _pk(jiuwenclaw_id, template_id), payload)

    async def delete(self, jiuwenclaw_id: str, template_id: int) -> bool:
        return await self._handler.delete(_TABLE, _pk(jiuwenclaw_id, template_id))


def _matches_model_type(row_model_type: Any, filter_type: str) -> bool:
    if isinstance(row_model_type, str):
        return row_model_type == filter_type
    if isinstance(row_model_type, list):
        return filter_type in row_model_type
    return str(row_model_type) == filter_type
