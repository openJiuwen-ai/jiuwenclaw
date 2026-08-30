"""AgentServer 企业配置读库：企业版专属
   不支持sqlite，因为本地文件无法跨 pod(gateway跟agentserver) 共享。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from jiuwenswarm.common.utils import logger
from jiuwenswarm.gateway.config.enterprise.instance_scope import (
    apply_instance_scope as _apply_instance_scope,
    instance_scoped_store_names,
    list_records_requires_bound_instance,
    resolve_gateway_instance_id,
)
from jiuwenswarm.gateway.config.enterprise.tables import init_all_tables

from openjiuwen_runtime.foundation.db.utils import is_mysql, is_postgresql
from openjiuwen_runtime.foundation.db.mysql_handler import MySQLHandler
from openjiuwen_runtime.foundation.db.postgresql_handler import PostgreSQLHandler


from .schemas import SLOT_ENTITY_TABLE, TemplateRefSlot

_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
INSTANCE_SCOPED_TABLES = instance_scoped_store_names()

_remote_handler: Any | None = None
_remote_handler_key: str | None = None
_remote_handler_lock = asyncio.Lock()


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


def _parse_json_string(value: str) -> Any:
    text = value.strip()
    if not text or text[0] not in "{[":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    """将 DB 行转为 dict；兼容 ``dict`` / SQLAlchemy Row / ORM 实例。"""
    if isinstance(row, dict):
        items = dict(row)
    elif hasattr(row, "keys") and callable(getattr(row, "keys")) and not hasattr(row, "__table__"):
        # SQLAlchemy Row（ORM 实例有 __table__，走 vars 分支）
        items = {k: row[k] for k in row.keys()}
    else:
        # ORM 实例：取 __dict__，剔除 SQLAlchemy 内部 _sa_ 字段
        items = {k: v for k, v in vars(row).items() if not k.startswith("_sa_")}
    out: dict[str, Any] = {}
    for key, value in items.items():
        if isinstance(value, str):
            out[key] = _parse_json_string(value)
        else:
            out[key] = value
    return out

async def _dispose_remote_handler_unlocked() -> None:
    """释放进程内缓存的远程 handler（调用方须已持有 ``_remote_handler_lock``）。"""
    global _remote_handler, _remote_handler_key

    handler = _remote_handler
    _remote_handler = None
    _remote_handler_key = None
    if handler is None:
        return
    try:
        await handler.disconnect()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[enterprise_config] dispose remote gateway db handler failed: %s",
            exc,
        )


async def _get_remote_handler() -> Any:
    """agentserver 读取共享的 gateway 配置库（远程 MySQL/PostgreSQL）。
    handler 在 agentserver 进程内缓存复用，避免每次查询重建连接池。
    pg 带 ``schema`` → search_path 自动隔离。配置变化时先 dispose 旧 handler。
    """
    global _remote_handler, _remote_handler_key

    db_type = os.getenv("GATEWAY_DB_TYPE", "").strip()
    db_host = os.getenv("GATEWAY_DB_HOST", "").strip()
    db_port = os.getenv("GATEWAY_DB_PORT", "").strip()
    db_user = os.getenv("GATEWAY_DB_USER", "root").strip()
    db_password = os.getenv("GATEWAY_DB_PASSWORD", "").strip()
    db_name = os.getenv("GATEWAY_DB_NAME", "gateway").strip()
    pg_schema = os.getenv("GATEWAY_PG_SCHEMA", "public").strip() or "public"
    key = f"{db_type}|{db_host}|{db_port}|{db_user}|{db_name}|{pg_schema}"

    async with _remote_handler_lock:
        if _remote_handler is not None and _remote_handler_key == key:
            return _remote_handler

        if _remote_handler is not None:
            logger.info(
                "[enterprise_config] gateway db config changed (%s -> %s), disposing old handler",
                _remote_handler_key,
                key,
            )
            await _dispose_remote_handler_unlocked()

        if is_postgresql(db_type):
            handler = PostgreSQLHandler(
                host=db_host,
                port=int(db_port),
                database=db_name,
                schema=pg_schema,
                user=db_user,
                password=db_password,
            )
        elif is_mysql(db_type):
            handler = MySQLHandler(
                host=db_host,
                port=int(db_port),
                database=db_name,
                user=db_user,
                password=db_password,
            )
        else:
            raise RuntimeError(
                f"unsupported GATEWAY_DB_TYPE={db_type!r}; use mysql or postgresql"
            )

        await handler.init_database()
        await handler.connect()
        await init_all_tables(handler)
        _remote_handler = handler
        _remote_handler_key = key
        logger.info("[enterprise_config] gateway db reader ready: key=%s", key)
        return handler


async def _list_records_remote(
    table: str,
    query: dict[str, Any],
    order_by: str,
) -> list[dict[str, Any]]:
    try:
        handler = await _get_remote_handler()
        rows = await handler.list_records(
            table, query, limit=10_000, offset=0, order_by=order_by or None
        )
        return [_row_to_dict(r) for r in rows]
    except Exception as exc:
        logger.error(
            "[enterprise_config] query %s failed: %s",
            table,
            exc,
        )
        raise


PERMISSIONS_CONFIG_TABLE = "permissions_config"


async def upsert_permissions_config(
    body: dict[str, Any],
    *,
    source: str = "runtime_persist",
) -> None:
    """按 ``jiuwenclaw_id`` upsert ``permissions_config``。"""
    jid = resolve_jiuwenclaw_id()
    if not jid:
        raise ValueError("JIUWENCLAW_ID is required for enterprise permissions persist")

    now = datetime.now(timezone.utc).isoformat()
    body_json = json.dumps(body, ensure_ascii=False)

    await _upsert_permissions_remote(jid, body_json, source=source, now=now)


async def _upsert_permissions_remote(
    jid: str,
    body_json: str,
    *,
    source: str,
    now: str,
) -> None:
    table = PERMISSIONS_CONFIG_TABLE
    handler = await _get_remote_handler()
    existing = await handler.get(table, {"jiuwenclaw_id": jid})
    if existing is not None:
        revision = int(_row_to_dict(existing).get("revision") or 1) + 1
        await handler.update(
            table,
            {"jiuwenclaw_id": jid},
            {
                "body": body_json,
                "source": source,
                "revision": revision,
                "updated_at": now,
            },
        )
    else:
        await handler.create(
            table,
            {
                "jiuwenclaw_id": jid,
                "body": body_json,
                "source": source,
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            },
        )


__all__ = (
    "INSTANCE_SCOPED_TABLES",
    "PERMISSIONS_CONFIG_TABLE",
    "apply_instance_scope",
    "fetch_template_by_slot",
    "list_records",
    "resolve_jiuwenclaw_id",
    "upsert_permissions_config",
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
    """
    if not _SAFE_IDENT.fullmatch(table or ""):
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
    return await _list_records_remote(table, query, order_by)
