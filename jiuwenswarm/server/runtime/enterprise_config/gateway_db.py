# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway 本地库：企业配置读库 facade（``GATEWAY_*``，不依赖 jiuwenclaw-ee）。

IO 实现见 ``gateway/storage/backends/db/reader``（与 Gateway 写库同环境变量）。
本模块负责实例隔离（``jiuwenclaw_id``）与模板槽位等业务封装。
"""

from __future__ import annotations

from typing import Any

from jiuwenswarm.common.utils import logger
from jiuwenswarm.gateway.config.enterprise.instance_scope import (
    apply_instance_scope as _apply_instance_scope,
    instance_scoped_store_names,
    list_records_requires_bound_instance,
    resolve_gateway_instance_id,
)
from jiuwenswarm.gateway.storage.backends.db import reader as _db_reader

from .schemas import SLOT_ENTITY_TABLE, TemplateRefSlot

# 与 catalog + cron_job 对齐；供测试与文档引用
INSTANCE_SCOPED_TABLES = instance_scoped_store_names()
PERMISSIONS_CONFIG_TABLE = _db_reader.PERMISSIONS_CONFIG_TABLE

resolve_gateway_db_path = _db_reader.resolve_gateway_db_path
use_remote_gateway_db = _db_reader.use_remote_gateway_db
is_gateway_db_available = _db_reader.is_gateway_db_available

# 别名：单测可 monkeypatch 本模块上的 ``_list_records_*``
_list_records_remote = _db_reader.list_records_remote
_list_records_sqlite = _db_reader.list_records_sqlite


def resolve_jiuwenclaw_id() -> str | None:
    """当前实例 id（``resolve_gateway_instance_id`` 别名，供 AgentServer 调用方）。"""
    return resolve_gateway_instance_id()


def apply_instance_scope(table: str, filters: dict[str, Any]) -> dict[str, Any]:
    """为 scoped 表查询附加 ``jiuwenclaw_id``（供读库与测试复用）。"""
    return _apply_instance_scope(
        table,
        filters,
        instance_id=resolve_gateway_instance_id(),
    )


async def upsert_permissions_config(
    body: dict[str, Any],
    *,
    source: str = "runtime_persist",
) -> None:
    """按 ``jiuwenclaw_id`` upsert ``permissions_config``（远程或本地 sqlite）。"""
    jid = resolve_jiuwenclaw_id()
    if not jid:
        raise ValueError("JIUWENCLAW_ID is required for enterprise permissions persist")
    await _db_reader.upsert_permissions_config(
        body,
        jiuwenclaw_id=jid,
        source=source,
    )


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
    """列表查询；scoped 表自动按 ``jiuwenclaw_id`` 隔离。

    ``is_enterprise()`` + ``GATEWAY_DB_HOST`` 时走远程 MySQL/PG；否则走本地 sqlite。
    """
    if not _db_reader.is_safe_ident(table or ""):
        logger.warning("[enterprise_config] invalid table name: %r", table)
        return []

    instance_id = resolve_gateway_instance_id()
    if list_records_requires_bound_instance(table, instance_id):
        logger.warning(
            "[enterprise_config] list_records skipped: jiuwenclaw_id not bound for table=%s",
            table,
        )
        return []

    query = apply_instance_scope(table, dict(filters or {}))
    if use_remote_gateway_db():
        return await _list_records_remote(table, query, order_by)
    return await _list_records_sqlite(table, query, order_by)


__all__ = (
    "INSTANCE_SCOPED_TABLES",
    "PERMISSIONS_CONFIG_TABLE",
    "apply_instance_scope",
    "fetch_template_by_slot",
    "is_gateway_db_available",
    "list_records",
    "resolve_gateway_db_path",
    "resolve_jiuwenclaw_id",
    "upsert_permissions_config",
    "use_remote_gateway_db",
)
