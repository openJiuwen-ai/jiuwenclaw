"""Gateway 本地库：连接管理与企业配置读库（``GATEWAY_*`` / ``manager_ws_client.infrastructure``）。"""

from __future__ import annotations

import importlib
import json
from typing import Any

from jiuwenclaw.gateway.channel_config_db import (
    _EXT_PKG,
    _ensure_extension_package,
    _resolve_manager_ws_client_root,
)
from jiuwenclaw.utils import logger

from .schemas import SLOT_ENTITY_TABLE, TemplateRefSlot


def _load_database_class() -> type:
    """经扩展命名空间加载 ``Database``（不修改 ``sys.path``）。"""
    ext_root = _resolve_manager_ws_client_root()
    if ext_root is None:
        raise ImportError("manager_ws_client extension not found")
    _ensure_extension_package(ext_root)
    db_mod = importlib.import_module(f"{_EXT_PKG}.infrastructure.db")
    return db_mod.Database

# Gateway 与 AgentServer 企业配置共用同一库连接（进程内单例）
_gateway_database = _load_database_class()()


def _parse_json_string(value: str) -> Any:
    """若字符串形如 JSON 对象/数组则解析，否则原样返回。"""
    text = value.strip()
    if not text or text[0] not in "{[":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        out = dict(row)
    elif hasattr(row, "model_dump"):
        out = row.model_dump(mode="json")
    else:
        field_names = getattr(row, "__dataclass_fields__", None) or getattr(
            row, "__annotations__", None
        )
        if not field_names:
            field_names = vars(row)
        out = {k: getattr(row, k) for k in field_names}

    for key, value in list(out.items()):
        if isinstance(value, str):
            parsed = _parse_json_string(value)
            if parsed is not value:
                out[key] = parsed
    return out


def _sort_by_order(rows: list[dict[str, Any]], order_by: str) -> list[dict[str, Any]]:
    """支持 ``priority DESC`` / ``priority ASC`` 或 ``-priority``。"""
    text = order_by.strip()
    if not text:
        return rows

    parts = text.split(None, 1)
    field = parts[0].strip()
    reverse = False
    if len(parts) > 1:
        reverse = parts[1].strip().upper() == "DESC"
    elif field.startswith("-"):
        reverse = True
        field = field[1:].strip()
    if not field:
        return rows

    def _key(row: dict[str, Any]) -> Any:
        value = row.get(field)
        if value is None:
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return value

    return sorted(rows, key=_key, reverse=reverse)


__all__ = ("fetch_template_by_slot", "list_records")


async def fetch_template_by_slot(
    slot: str,
    template_id: str,
) -> dict[str, Any] | None:
    """按 ``template_ref`` 槽位与 ``template_id`` 从 Gateway 库加载一条启用中的模板行。"""
    try:
        slot_key = TemplateRefSlot(slot)
    except ValueError as exc:
        raise ValueError(
            f"unknown template_ref slot {slot!r} "
            f"(known: {[s.value for s in TemplateRefSlot]})"
        ) from exc
    table = SLOT_ENTITY_TABLE[slot_key]
    ref = str(template_id or "").strip()
    if not ref:
        return None
    filters: dict[str, Any] = {"enabled": True, "template_id": ref}
    if table == "extension_config_template":
        filters["component"] = "agent_server"
    rows = await list_records(table, filters=filters)
    return rows[0] if rows else None


async def list_records(
    table: str,
    *,
    filters: dict[str, Any] | None = None,
    order_by: str = "",
) -> list[dict[str, Any]]:
    """列表查询；``filters`` 含 ``enabled`` 等条件。"""
    query = dict(filters or {})

    try:
        handler = await _gateway_database.ensure_ready(log_prefix="enterprise_config")
        rows = await handler.list_records(table, query, limit=10_000, offset=0)
        result = [_row_to_dict(r) for r in rows]
        return _sort_by_order(result, order_by) if order_by else result
    except Exception as exc:
        logger.warning("[enterprise_config] query %s failed: %s", table, exc)
        return []
