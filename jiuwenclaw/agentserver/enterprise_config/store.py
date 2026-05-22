"""配置生效策略与 model_template 持久化访问。"""

from __future__ import annotations

import json
import re
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw.utils import logger

from .db import ensure_db_handler_ready, get_db_handler, reset_db_handler
from .settings import EffectivePolicyDatabaseSettings, get_settings

_LOG = "[enterprise_config]"

_JSON_COLUMNS = frozenset(
    {
        "model_type",
        "model_tags",
        "parameters",
        "data",
        "channel_ids",
        "template_ref",
        "hook_config",
        "custom_config",
    }
)


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        raw = dict(row)
    else:
        raw = _orm_to_dict(row)
    return {k: _normalize_json(k, v) for k, v in raw.items()}


def _normalize_json(key: str, value: Any) -> Any:
    if isinstance(value, str) and key in _JSON_COLUMNS:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _orm_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "to_dict") and callable(row.to_dict):
        data = row.to_dict()
        if isinstance(data, dict):
            return dict(data)
    table = getattr(row, "__table__", None)
    if table is not None:
        return {col.name: getattr(row, col.name, None) for col in table.columns}
    return {}


class EffectivePolicyStore:
    """读取 ``config_effective_*`` 与 ``model_template``（表定义与 manager_ws_client 一致）。"""

    def __init__(self, settings: EffectivePolicyDatabaseSettings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def settings(self) -> EffectivePolicyDatabaseSettings:
        return self._settings

    async def ensure_connected(self) -> bool:
        try:
            await ensure_db_handler_ready(self._settings)
            return True
        except Exception as exc:
            logger.warning("%s policy database not available: %s", _LOG, exc)
            return False

    async def list_records(
        self,
        table: str,
        *,
        jiuwenclaw_id: str,
        filters: dict[str, Any] | None = None,
        order_by: str = "",
    ) -> list[dict[str, Any]]:
        if not await self.ensure_connected():
            return []
        query = {"jiuwenclaw_id": jiuwenclaw_id, **(filters or {})}
        try:
            handler = get_db_handler()
            rows = await handler.list_records(table, filters=query)
            result = [_row_to_dict(r) for r in rows]
            return _sort_by_order(result, order_by) if order_by else result
        except Exception as exc:
            logger.warning("%s query %s failed: %s", _LOG, table, exc)
            return []

    async def list_enabled_service_policies(
        self, jiuwenclaw_id: str
    ) -> list[dict[str, Any]]:
        return await self.list_records(
            "config_effective_service_policy",
            jiuwenclaw_id=jiuwenclaw_id,
            filters={"enabled": True},
            order_by="priority DESC",
        )

    async def list_enabled_agent_policies(
        self, jiuwenclaw_id: str, service_policy_id: int
    ) -> list[dict[str, Any]]:
        return await self.list_records(
            "config_effective_agent_policy",
            jiuwenclaw_id=jiuwenclaw_id,
            filters={"enabled": True, "service_policy_id": service_policy_id},
            order_by="priority DESC",
        )

    async def get_enabled_global_policy(
        self, jiuwenclaw_id: str
    ) -> dict[str, Any] | None:
        rows = await self.list_records(
            "config_effective_global_policy",
            jiuwenclaw_id=jiuwenclaw_id,
            filters={"enabled": True},
            order_by="priority DESC",
        )
        return rows[0] if rows else None

    async def lookup_template_mapping_ref(
        self,
        jiuwenclaw_id: str,
        *,
        user_id: str | None = None,
        group_id: str | None = None,
    ) -> str | None:
        uid = str(user_id or "").strip()
        gid = str(group_id or "").strip()
        if not uid and not gid:
            return None
        rows = await self.list_records(
            "config_default_template_mapping",
            jiuwenclaw_id=jiuwenclaw_id,
            filters={"enabled": True},
        )
        if not rows:
            return None
        rows.sort(key=lambda row: int(row.get("priority") or 0), reverse=True)
        if uid:
            for row in rows:
                if str(row.get("user_id") or "").strip() == uid:
                    ref = str(row.get("template_id") or "").strip()
                    if ref:
                        return ref
        if gid:
            for row in rows:
                if str(row.get("group_id") or "").strip() == gid:
                    ref = str(row.get("template_id") or "").strip()
                    if ref:
                        return ref
        return None

    async def get_model_template(
        self, jiuwenclaw_id: str, template_ref: str
    ) -> dict[str, Any] | None:
        ref = str(template_ref or "").strip()
        if not ref:
            return None
        if ref.isdigit():
            filters = {"id": int(ref), "enabled": True}
        else:
            filters = {"model_id": ref, "enabled": True}
        rows = await self.list_records(
            "model_template",
            jiuwenclaw_id=jiuwenclaw_id,
            filters=filters,
        )
        return rows[0] if rows else None

    async def get_extension_config_template(
        self, jiuwenclaw_id: str, template_ref: str
    ) -> dict[str, Any] | None:
        """按 ``template_id`` 或数字 id 查 ``extension_config_template`` 表。

        注意：该表无 ``jiuwenclaw_id`` 列，``template_id`` 全局唯一，
        因此不按实例过滤。
        """
        ref = str(template_ref or "").strip()
        if not ref:
            return None
        if not await self.ensure_connected():
            return None
        if ref.isdigit():
            filters = {"id": int(ref), "enabled": True}
        else:
            filters = {"template_id": ref, "enabled": True}
        try:
            handler = get_db_handler()
            rows = await handler.list_records(
                "extension_config_template",
                filters=filters,
            )
            if rows:
                return _row_to_dict(rows[0])
        except Exception as exc:
            logger.warning("%s query extension_config_template failed: %s", _LOG, exc)
        return None


def _sort_by_order(rows: list[dict[str, Any]], order_by: str) -> list[dict[str, Any]]:
    """对查询结果按 order_by 排序。

    仅支持 ``column DESC`` 或 ``column ASC`` 格式。
    """
    if not order_by:
        return rows
    parts = order_by.strip().split()
    if len(parts) != 2:
        return rows
    column, direction = parts
    reverse = direction.upper() == "DESC"

    def _sort_key(row: dict[str, Any]) -> Any:
        value = row.get(column)
        if isinstance(value, (int, float)):
            return value
        try:
            return int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return value or 0

    return sorted(rows, key=_sort_key, reverse=reverse)


_store: EffectivePolicyStore | None = None


def get_store(settings: EffectivePolicyDatabaseSettings | None = None) -> EffectivePolicyStore:
    global _store
    if _store is None:
        _store = EffectivePolicyStore(settings)
    return _store


def reset_store() -> None:
    global _store
    _store = None
