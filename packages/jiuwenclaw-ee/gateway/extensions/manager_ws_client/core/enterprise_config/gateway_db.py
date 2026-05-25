# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway 本地库：连接管理与企业配置读库（``GATEWAY_*`` / ``manager_ws_client.infrastructure``）。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openjiuwen_runtime.foundation.log import get_logger

from ...infrastructure.db import Database
from ...infrastructure.utils import resolve_jiuwenclaw_id as _default_resolve_jiuwenclaw_id
from .schemas import SLOT_ENTITY_TABLE, TemplateRefSlot

logger = get_logger(__name__)

_db = Database(relative_root=Path(__file__).resolve().parents[2])

_INSTANCE_SCOPED_TABLES = frozenset({
    "config_effective_service_policy",
    "config_effective_agent_policy",
    "config_effective_global_policy",
    "config_default_template_mapping",
    "model_template",
})

_jiuwenclaw_id_resolver: Callable[[], str | None] = _default_resolve_jiuwenclaw_id


def set_resolve_jiuwenclaw_id(resolver: Callable[[], str | None]) -> None:
    """注入 ``jiuwenclaw_id`` 解析函数（Gateway / AgentServer 可各自注册实现）。"""
    global _jiuwenclaw_id_resolver
    _jiuwenclaw_id_resolver = resolver


def resolve_jiuwenclaw_id() -> str | None:
    return _jiuwenclaw_id_resolver()


def apply_instance_scope(table: str, filters: dict[str, Any]) -> dict[str, Any]:
    """为策略/映射/模型表查询附加 ``jiuwenclaw_id`` 隔离条件（供读库与测试复用）。"""
    query = dict(filters)
    if table not in _INSTANCE_SCOPED_TABLES:
        return query
    jiuwenclaw_id = resolve_jiuwenclaw_id()
    if jiuwenclaw_id:
        query["jiuwenclaw_id"] = jiuwenclaw_id
    return query


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
    if table == "model_template":
        jiuwenclaw_id = resolve_jiuwenclaw_id()
        if jiuwenclaw_id:
            filters["jiuwenclaw_id"] = jiuwenclaw_id
    rows = await list_records(table, filters=filters)
    return rows[0] if rows else None


async def list_records(
    table: str,
    *,
    filters: dict[str, Any] | None = None,
    order_by: str = "",
) -> list[dict[str, Any]]:
    """列表查询；``filters`` 含 ``enabled`` 等条件。策略/映射/模型表自动按 ``jiuwenclaw_id`` 隔离。"""
    query = apply_instance_scope(table, dict(filters or {}))

    try:
        handler = await _db.ensure_ready(log_prefix="enterprise_config")
        rows = await handler.list_records(table, query, limit=10_000, offset=0)
        result = [_row_to_dict(r) for r in rows]
        return _sort_by_order(result, order_by) if order_by else result
    except Exception as exc:
        logger.warning("[enterprise_config] query %s failed: %s", table, exc)
        return []


__all__ = (
    "apply_instance_scope",
    "fetch_template_by_slot",
    "list_records",
    "resolve_jiuwenclaw_id",
    "set_resolve_jiuwenclaw_id",
)
