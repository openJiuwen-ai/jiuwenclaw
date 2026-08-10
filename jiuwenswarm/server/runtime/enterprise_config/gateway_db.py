"""Gateway 本地库：企业配置读库（``GATEWAY_*`` / aiosqlite，不依赖 jiuwenclaw-ee）。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import aiosqlite

from jiuwenswarm.common.utils import get_user_workspace_dir, logger

from .schemas import SLOT_ENTITY_TABLE, TemplateRefSlot

_DB_PATH: str | None = None
_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INSTANCE_SCOPED_TABLES = frozenset(
    {
        "config_effective_service_policy",
        "config_effective_agent_policy",
        "config_effective_global_policy",
        "config_default_template_mapping",
        "model_template",
        "embedding_template",
        "log_masking_rule",
    }
)


def resolve_gateway_db_path() -> str | None:
    """解析 Gateway SQLite 路径；未配置或不存在时返回 None。"""
    global _DB_PATH
    if _DB_PATH is not None:
        return _DB_PATH

    explicit = (
        os.getenv("GATEWAY_SQLITE_PATH", "").strip()
        or os.getenv("JIUWENSWARM_GATEWAY_DB_PATH", "").strip()
        or os.getenv("JIUWENCLAW_GATEWAY_DB_PATH", "").strip()
        or os.getenv("MANAGER_WS_CLIENT_SQLITE_PATH", "").strip()
    )
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            data_dir = (
                os.getenv("JIUWENSWARM_DATA_DIR", "").strip()
                or os.getenv("JIUWENCLAW_DATA_DIR", "").strip()
            )
            path = (Path(data_dir) / path) if data_dir else path
        path = path.resolve()
        if path.is_file():
            _DB_PATH = str(path)
            return _DB_PATH
        logger.warning("[enterprise_config] GATEWAY_SQLITE_PATH not found: %s", path)
        return None

    data_dir = (
        os.getenv("JIUWENSWARM_DATA_DIR", "").strip()
        or os.getenv("JIUWENCLAW_DATA_DIR", "").strip()
    )
    if data_dir:
        root = Path(data_dir).expanduser().resolve()
        for candidate in (
            root / "gateway.db",
            root / "agent_client.db",
            root / "gateway" / "agent_client.db",
        ):
            if candidate.is_file():
                _DB_PATH = str(candidate)
                return _DB_PATH

    try:
        from jiuwenswarm.common.config import get_config

        sqlite_path = (
            (get_config().get("extensions") or {})
            .get("agent_client_rest", {})
            .get("database", {})
            .get("sqlite_path")
        )
        if isinstance(sqlite_path, str) and sqlite_path.strip():
            configured = Path(sqlite_path.strip()).expanduser()
            if configured.is_file():
                _DB_PATH = str(configured.resolve())
                return _DB_PATH
    except Exception as exc:
        logger.debug("[enterprise_config] read sqlite_path from config failed: %s", exc)

    fallback = get_user_workspace_dir() / "gateway" / "agent_client.db"
    if fallback.is_file():
        _DB_PATH = str(fallback.resolve())
        return _DB_PATH
    return None


def resolve_jiuwenclaw_id() -> str | None:
    """从环境变量读取当前实例 id；未设置时返回 ``None``。

    优先 ``JIUWENCLAW_ID`` / ``JIUWENSWARM_ID``（Manager WS register.ack 写入）；
    兼容旧的 ``*_PROVISIONED_INSTANCE_ID`` / ``GATEWAY_INSTANCE_ID``。
    """
    instance_id = (
        os.getenv("JIUWENCLAW_ID", "").strip()
        or os.getenv("JIUWENSWARM_ID", "").strip()
        or os.getenv("JIUWENSWARM_PROVISIONED_INSTANCE_ID", "").strip()
        or os.getenv("JIUWENCLAW_PROVISIONED_INSTANCE_ID", "").strip()
        or os.getenv("GATEWAY_INSTANCE_ID", "").strip()
    )
    return instance_id or None


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
    text = value.strip()
    if not text or text[0] not in "{[":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if isinstance(value, str):
            parsed = _parse_json_string(value)
            out[key] = parsed
        else:
            out[key] = value
    return out


def _sort_by_order(rows: list[dict[str, Any]], order_by: str) -> list[dict[str, Any]]:
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


__all__ = (
    "apply_instance_scope",
    "fetch_template_by_slot",
    "list_records",
    "resolve_gateway_db_path",
    "resolve_jiuwenclaw_id",
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
    if not _SAFE_IDENT.fullmatch(table or ""):
        logger.warning("[enterprise_config] invalid table name: %r", table)
        return []

    db_path = resolve_gateway_db_path()
    if not db_path:
        return []

    query = apply_instance_scope(table, dict(filters or {}))
    where_parts: list[str] = []
    params: list[Any] = []
    for key, value in query.items():
        if not _SAFE_IDENT.fullmatch(str(key)):
            continue
        where_parts.append(f"{key} = ?")
        if isinstance(value, bool):
            params.append(1 if value else 0)
        else:
            params.append(value)

    sql = f"SELECT * FROM {table}"
    if where_parts:
        sql = f"{sql} WHERE {' AND '.join(where_parts)}"

    try:
        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        result = [_row_to_dict(r) for r in rows]
        return _sort_by_order(result, order_by) if order_by else result
    except Exception as exc:
        logger.warning("[enterprise_config] query %s failed: %s", table, exc)
        return []
