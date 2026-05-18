"""读取 Gateway ``agent_client.db``（SQLite）。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import aiosqlite

from jiuwenclaw.utils import get_user_workspace_dir, logger

_DB_PATH: str | None = None


def resolve_gateway_db_path() -> str | None:
    global _DB_PATH
    if _DB_PATH is not None:
        return _DB_PATH

    explicit = os.getenv("JIUWENCLAW_GATEWAY_DB_PATH", "").strip()
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if path.is_file():
            _DB_PATH = str(path)
            return _DB_PATH
        logger.warning("[enterprise_config] JIUWENCLAW_GATEWAY_DB_PATH not found: %s", path)
        return None

    data_dir = os.getenv("JIUWENCLAW_DATA_DIR", "").strip()
    if data_dir:
        root = Path(data_dir).expanduser().resolve()
        for candidate in (
            root / "agent_client.db",
            root / "gateway" / "agent_client.db",
        ):
            if candidate.is_file():
                _DB_PATH = str(candidate)
                return _DB_PATH

    try:
        from jiuwenclaw.config import get_config

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


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if isinstance(value, str) and key in {
            "model_type",
            "model_tags",
            "parameters",
            "data",
            "channel_ids",
        }:
            try:
                out[key] = json.loads(value)
            except json.JSONDecodeError:
                out[key] = value
        else:
            out[key] = value
    return out


async def fetch_all(
    table: str,
    *,
    jiuwenclaw_id: str,
    extra_where: str = "",
    extra_params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    db_path = resolve_gateway_db_path()
    if not db_path:
        return []

    where = "jiuwenclaw_id = ?"
    params: list[Any] = [jiuwenclaw_id]
    if extra_where:
        where = f"{where} AND {extra_where}"
        params.extend(extra_params)

    sql = f"SELECT * FROM {table} WHERE {where}"
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
    return [_row_to_dict(r) for r in rows]


async def lookup_model_template_mapping_ref(
    jiuwenclaw_id: str,
    *,
    user_id: str | None = None,
    group_id: str | None = None,
) -> str | None:
    """按 ``user_id`` / ``group_id`` 查 ``config_default_template_mapping``，返回 ``template_id``。"""
    uid = str(user_id or "").strip()
    gid = str(group_id or "").strip()
    if not uid and not gid:
        return None

    rows = await fetch_all(
        "config_default_template_mapping",
        jiuwenclaw_id=jiuwenclaw_id,
        extra_where="enabled = 1 AND template_type = 'model'",
    )
    if not rows:
        return None

    def _priority(row: dict[str, Any]) -> int:
        data = row.get("data")
        if isinstance(data, dict):
            try:
                return int(data.get("priority") or 0)
            except (TypeError, ValueError):
                return 0
        return 0

    rows.sort(key=_priority, reverse=True)

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


async def fetch_model_template(
    jiuwenclaw_id: str, template_ref: str
) -> dict[str, Any] | None:
    ref = str(template_ref or "").strip()
    if not ref:
        return None
    if ref.isdigit():
        where = "id = ? AND enabled = 1"
        params: tuple[Any, ...] = (int(ref),)
    else:
        where = "model_id = ? AND enabled = 1"
        params = (ref,)
    rows = await fetch_all(
        "model_template",
        jiuwenclaw_id=jiuwenclaw_id,
        extra_where=where,
        extra_params=params,
    )
    return rows[0] if rows else None
