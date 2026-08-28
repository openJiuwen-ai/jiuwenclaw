"""Gateway 本地库：企业配置读库（``GATEWAY_*``，不依赖 jiuwenclaw-ee）。

- 单机 / 未配远程库：aiosqlite 读本地文件（``resolve_gateway_db_path``）。
- 企业（``JIUWENSWARM_EDITION=enterprise``）且 ``GATEWAY_DB_HOST`` 有值：
  连 MySQL/PostgreSQL（与 Gateway 同一套 env），**禁止**再回退本地 sqlite。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import aiosqlite

from jiuwenswarm.common.utils import get_user_workspace_dir, logger
from jiuwenswarm.gateway.config.enterprise.instance_scope import (
    apply_instance_scope as _apply_instance_scope,
    instance_scoped_store_names,
    list_records_requires_bound_instance,
    resolve_gateway_instance_id,
)

from .schemas import SLOT_ENTITY_TABLE, TemplateRefSlot

_DB_PATH: str | None = None
_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 与 catalog + cron_job 对齐；供测试与文档引用
INSTANCE_SCOPED_TABLES = instance_scoped_store_names()

_remote_engine: Any | None = None
_remote_engine_key: str | None = None
_remote_engine_lock = asyncio.Lock()


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
    """当前实例 id（``resolve_gateway_instance_id`` 别名，供 AgentServer 调用方）。"""
    return resolve_gateway_instance_id()


def apply_instance_scope(table: str, filters: dict[str, Any]) -> dict[str, Any]:
    """为 scoped 表查询附加 ``jiuwenclaw_id``（供读库与测试复用）。"""
    return _apply_instance_scope(
        table,
        filters,
        instance_id=resolve_gateway_instance_id(),
    )


def use_remote_gateway_db() -> bool:
    """企业远程库：``is_enterprise()`` 且 ``GATEWAY_DB_HOST`` 非空。

    单机版（``JIUWENSWARM_EDITION`` 非 enterprise）仍走本地 sqlite，行为不变。
    """
    from jiuwenswarm.common.local_env_config import is_enterprise

    if not is_enterprise():
        return False
    return bool(os.getenv("GATEWAY_DB_HOST", "").strip())


def _parse_json_string(value: str) -> Any:
    text = value.strip()
    if not text or text[0] not in "{[":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    """将 DB 行转为 dict；兼容 ``dict`` / aiosqlite.Row / SQLAlchemy Row。"""
    if isinstance(row, dict):
        items = dict(row)
    else:
        items = {k: row[k] for k in row.keys()}
    out: dict[str, Any] = {}
    for key, value in items.items():
        if isinstance(value, str):
            out[key] = _parse_json_string(value)
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


def _gateway_db_pool_kwargs() -> dict[str, Any]:
    def _int_env(*names: str, default: int) -> int:
        for name in names:
            raw = os.getenv(name, "").strip()
            if not raw:
                continue
            try:
                return int(raw)
            except ValueError:
                continue
        return default

    return {
        "pool_size": _int_env("GATEWAY_DB_POOL_SIZE", "RUNTIME_DB_POOL_SIZE", default=2),
        "max_overflow": _int_env(
            "GATEWAY_DB_MAX_OVERFLOW", "RUNTIME_DB_MAX_OVERFLOW", default=20
        ),
        "pool_timeout": _int_env(
            "GATEWAY_DB_POOL_TIMEOUT", "RUNTIME_DB_POOL_TIMEOUT", default=30
        ),
        "pool_recycle": 3600,
        "pool_pre_ping": True,
    }


def _remote_db_type() -> str:
    raw = (
        os.getenv("GATEWAY_DB_TYPE", "").strip()
        or os.getenv("DB_TYPE", "").strip()
        or "mysql"
    )
    return raw.lower()


async def _dispose_remote_engine_unlocked() -> None:
    """释放进程内缓存的远程 engine（调用方须已持有 ``_remote_engine_lock``）。"""
    global _remote_engine, _remote_engine_key

    engine = _remote_engine
    _remote_engine = None
    _remote_engine_key = None
    if engine is None:
        return
    try:
        await engine.dispose()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[enterprise_config] dispose remote gateway db engine failed: %s",
            exc,
        )


async def _dispose_remote_engine() -> None:
    """释放进程内缓存的远程 engine 连接池。"""
    async with _remote_engine_lock:
        await _dispose_remote_engine_unlocked()


async def _get_remote_engine() -> Any:
    """进程内复用远程读库 engine；连接失败抛错，不回退 sqlite。

    配置（host/type/port/user/name）变化时先 ``dispose`` 旧 engine，避免连接池泄漏。
    检查 / dispose / 创建 / 赋值 由 ``_remote_engine_lock`` 串行化，避免并发覆盖泄漏。
    """
    global _remote_engine, _remote_engine_key

    db_type = _remote_db_type()
    db_host = os.getenv("GATEWAY_DB_HOST", "").strip()
    db_port = os.getenv("GATEWAY_DB_PORT", "").strip()
    db_user = os.getenv("GATEWAY_DB_USER", "root").strip()
    db_password = os.getenv("GATEWAY_DB_PASSWORD", "").strip()
    db_name = os.getenv("GATEWAY_DB_NAME", "gateway").strip()
    if not db_port:
        db_port = "5432" if db_type in {"postgresql", "postgres", "pg"} else "3306"

    key = f"{db_type}|{db_host}|{db_port}|{db_user}|{db_name}"

    async with _remote_engine_lock:
        if _remote_engine is not None and _remote_engine_key == key:
            return _remote_engine

        if _remote_engine is not None:
            logger.info(
                "[enterprise_config] remote gateway db config changed (%s -> %s), disposing old engine",
                _remote_engine_key,
                key,
            )
            await _dispose_remote_engine_unlocked()

        from sqlalchemy.ext.asyncio import create_async_engine

        user = quote_plus(db_user)
        password = quote_plus(db_password)
        if db_type in {"postgresql", "postgres", "pg"}:
            url = (
                f"postgresql+asyncpg://{user}:{password}"
                f"@{db_host}:{db_port}/{db_name}"
            )
        elif db_type in {"mysql", "mariadb"}:
            url = (
                f"mysql+aiomysql://{user}:{password}"
                f"@{db_host}:{db_port}/{db_name}?charset=utf8mb4"
            )
        else:
            raise RuntimeError(
                f"unsupported GATEWAY_DB_TYPE={db_type!r} for remote enterprise_config read; "
                "use mysql or postgresql, or unset GATEWAY_DB_HOST for sqlite"
            )

        engine = create_async_engine(url, **_gateway_db_pool_kwargs())
        _remote_engine = engine
        _remote_engine_key = key
        logger.info(
            "[enterprise_config] remote gateway db reader ready: type=%s %s:%s/%s",
            db_type,
            db_host,
            db_port,
            db_name,
        )
        return engine


def _build_filter_clause(
    query: dict[str, Any],
    *,
    named: bool,
) -> tuple[str, list[Any] | dict[str, Any]]:
    where_parts: list[str] = []
    if named:
        params: dict[str, Any] = {}
        for idx, (key, value) in enumerate(query.items()):
            if not _SAFE_IDENT.fullmatch(str(key)):
                continue
            pname = f"p{idx}"
            where_parts.append(f"{key} = :{pname}")
            params[pname] = (1 if value else 0) if isinstance(value, bool) else value
        clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
        return clause, params

    params_list: list[Any] = []
    for key, value in query.items():
        if not _SAFE_IDENT.fullmatch(str(key)):
            continue
        where_parts.append(f"{key} = ?")
        params_list.append((1 if value else 0) if isinstance(value, bool) else value)
    clause = f" WHERE {' AND '.join(where_parts)}" if where_parts else ""
    return clause, params_list


async def _list_records_remote(
    table: str,
    query: dict[str, Any],
    order_by: str,
) -> list[dict[str, Any]]:
    from sqlalchemy import text

    clause, params = _build_filter_clause(query, named=True)
    sql = f"SELECT * FROM {table}{clause}"
    try:
        engine = await _get_remote_engine()
        async with engine.connect() as conn:
            result = await conn.execute(text(sql), params)
            rows = result.fetchall()
        mapped = [_row_to_dict(r) for r in rows]
        return _sort_by_order(mapped, order_by) if order_by else mapped
    except Exception as exc:
        logger.error(
            "[enterprise_config] remote query %s failed (no sqlite fallback): %s",
            table,
            exc,
        )
        raise


async def _list_records_sqlite(
    table: str,
    query: dict[str, Any],
    order_by: str,
) -> list[dict[str, Any]]:
    db_path = resolve_gateway_db_path()
    if not db_path:
        return []

    clause, params = _build_filter_clause(query, named=False)
    sql = f"SELECT * FROM {table}{clause}"
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


PERMISSIONS_CONFIG_TABLE = "permissions_config"


def is_gateway_db_available() -> bool:
    """企业配置库是否可用：远程（有 HOST）或本地 sqlite 文件存在。"""
    if use_remote_gateway_db():
        return True
    return bool(resolve_gateway_db_path())


async def upsert_permissions_config(
    body: dict[str, Any],
    *,
    source: str = "runtime_persist",
) -> None:
    """按 ``jiuwenclaw_id`` upsert ``permissions_config``（远程或本地 sqlite）。"""
    jid = resolve_jiuwenclaw_id()
    if not jid:
        raise ValueError("JIUWENCLAW_ID is required for enterprise permissions persist")

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    body_json = json.dumps(body, ensure_ascii=False)

    if use_remote_gateway_db():
        await _upsert_permissions_remote(jid, body_json, source=source, now=now)
        return
    await _upsert_permissions_sqlite(jid, body_json, source=source, now=now)


async def _upsert_permissions_remote(
    jid: str,
    body_json: str,
    *,
    source: str,
    now: str,
) -> None:
    from sqlalchemy import text

    table = PERMISSIONS_CONFIG_TABLE
    if not _SAFE_IDENT.fullmatch(table):
        raise RuntimeError(f"invalid table name: {table!r}")

    engine = await _get_remote_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                f"SELECT id, revision FROM {table} WHERE jiuwenclaw_id = :jid"
            ),
            {"jid": jid},
        )
        existing = result.fetchone()
        if existing is not None:
            revision = int(existing[1] or 1) + 1
            await conn.execute(
                text(
                    f"""
                    UPDATE {table}
                    SET body = :body, source = :source, revision = :revision,
                        updated_at = :updated_at
                    WHERE jiuwenclaw_id = :jid
                    """
                ),
                {
                    "body": body_json,
                    "source": source,
                    "revision": revision,
                    "updated_at": now,
                    "jid": jid,
                },
            )
        else:
            await conn.execute(
                text(
                    f"""
                    INSERT INTO {table}
                    (jiuwenclaw_id, body, source, revision, created_at, updated_at)
                    VALUES (:jid, :body, :source, 1, :created_at, :updated_at)
                    """
                ),
                {
                    "jid": jid,
                    "body": body_json,
                    "source": source,
                    "created_at": now,
                    "updated_at": now,
                },
            )


async def _upsert_permissions_sqlite(
    jid: str,
    body_json: str,
    *,
    source: str,
    now: str,
) -> None:
    db_path = resolve_gateway_db_path()
    if not db_path:
        raise RuntimeError("gateway db not available")

    table = PERMISSIONS_CONFIG_TABLE
    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(
            f"SELECT id, revision FROM {table} WHERE jiuwenclaw_id = ?",
            (jid,),
        ) as cursor:
            existing = await cursor.fetchone()
        if existing is not None:
            revision = int(existing[1] or 1) + 1
            await conn.execute(
                f"""
                UPDATE {table}
                SET body = ?, source = ?, revision = ?, updated_at = ?
                WHERE jiuwenclaw_id = ?
                """,
                (body_json, source, revision, now, jid),
            )
        else:
            await conn.execute(
                f"""
                INSERT INTO {table}
                (jiuwenclaw_id, body, source, revision, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (jid, body_json, source, now, now),
            )
        await conn.commit()


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
    if use_remote_gateway_db():
        return await _list_records_remote(table, query, order_by)
    return await _list_records_sqlite(table, query, order_by)
