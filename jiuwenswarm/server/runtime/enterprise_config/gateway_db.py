# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway 本地库：企业配置读库 facade（``GATEWAY_*``，不依赖 jiuwenclaw-ee）。

IO 实现见 ``gateway/storage/backends/db/reader``（与 Gateway 写库同环境变量）。
每网关独立数据库，查询不加实例行级隔离。
"""

from __future__ import annotations

from typing import Any

from jiuwenswarm.common.utils import logger
from jiuwenswarm.gateway.storage.backends.db import reader as _db_reader

from .schemas import SLOT_ENTITY_TABLE, TemplateRefSlot

PERMISSIONS_CONFIG_TABLE = _db_reader.PERMISSIONS_CONFIG_TABLE

resolve_gateway_db_path = _db_reader.resolve_gateway_db_path
use_remote_gateway_db = _db_reader.use_remote_gateway_db
is_gateway_db_available = _db_reader.is_gateway_db_available

# 别名：单测可 monkeypatch 本模块上的 ``_list_records_*``
_list_records_remote = _db_reader.list_records_remote
_list_records_sqlite = _db_reader.list_records_sqlite


async def upsert_permissions_config(
    body: dict[str, Any],
    *,
    source: str = "runtime_persist",
) -> None:
    """单例行 upsert ``permissions_config``（远程或本地 sqlite）。"""
    await _db_reader.upsert_permissions_config(body, source=source)


async def fetch_template_by_slot(
    slot: str,
    template_id: str,
) -> dict[str, Any] | None:
    """按 ``template_ref`` 槽位与 ``template_id`` 加载一条启用中的模板行。"""
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
    rows = await list_records(table, filters=filters)
    return rows[0] if rows else None


async def list_records(
    table: str,
    *,
    filters: dict[str, Any] | None = None,
    order_by: str = "",
) -> list[dict[str, Any]]:
    """列表查询（每网关独立 DB，不加实例隔离列）。

    ``is_enterprise()`` + ``GATEWAY_DB_HOST`` 时走远程 MySQL/PG；否则走本地 sqlite。
    """
    if not _db_reader.is_safe_ident(table or ""):
        logger.warning("[enterprise_config] invalid table name: %r", table)
        return []

    query = dict(filters or {})
    if use_remote_gateway_db():
        return await _list_records_remote(table, query, order_by)
    return await _list_records_sqlite(table, query, order_by)


__all__ = (
    "PERMISSIONS_CONFIG_TABLE",
    "fetch_template_by_slot",
    "is_gateway_db_available",
    "list_records",
    "resolve_gateway_db_path",
    "upsert_permissions_config",
    "use_remote_gateway_db",
)
