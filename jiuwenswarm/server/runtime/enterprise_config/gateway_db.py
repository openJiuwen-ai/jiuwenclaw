# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""Gateway 本地库：企业配置读库 facade（企业版）。

委托 ``gateway/storage/backends/db/reader`` 访问；每网关独立数据库，查询不加实例行级隔离。
"""

from __future__ import annotations

from typing import Any

from jiuwenswarm.common.utils import logger
from jiuwenswarm.gateway.storage.backends.db import reader as _db_reader

from .schemas import SLOT_ENTITY_TABLE, TemplateRefSlot

PERMISSIONS_CONFIG_TABLE = _db_reader.PERMISSIONS_CONFIG_TABLE


async def upsert_permissions_config(
    body: dict[str, Any],
    *,
    source: str = "runtime_persist",
) -> None:
    """单例行 upsert ``permissions_config``。"""
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
    """列表查询（每网关独立 DB，不加实例隔离列）。"""
    if not _db_reader.is_safe_ident(table or ""):
        logger.warning("[enterprise_config] invalid table name: %r", table)
        return []
    return await _db_reader.list_records(table, query=filters, order_by=order_by)


__all__ = (
    "PERMISSIONS_CONFIG_TABLE",
    "fetch_template_by_slot",
    "list_records",
    "upsert_permissions_config",
)
