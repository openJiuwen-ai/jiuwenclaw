# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Permissions 配置加载：企业版 Gateway DB 优先，否则回落 config.yaml。"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable

import aiosqlite

logger = logging.getLogger(__name__)

PERMISSIONS_CONFIG_TABLE = "permissions_config"

_cached_permissions: dict[str, Any] | None = None
_cache_source: str | None = None


def is_enterprise_runtime() -> bool:
    return bool(os.getenv("AGENT_RUNTIME", "").strip())


def clear_permissions_config_cache() -> None:
    global _cached_permissions, _cache_source
    _cached_permissions = None
    _cache_source = None


def _load_permissions_from_yaml() -> dict[str, Any]:
    from jiuwenswarm.common.config import get_config

    raw = (get_config() or {}).get("permissions")
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    return {}


def _set_cache(body: dict[str, Any], source: str) -> None:
    global _cached_permissions, _cache_source
    _cached_permissions = copy.deepcopy(body)
    _cache_source = source


def get_effective_permissions_config(*, force_reload: bool = False) -> dict[str, Any]:
    """返回生效的 ``permissions`` 段（企业版：Gateway DB → YAML；其他：YAML）。"""
    global _cached_permissions, _cache_source

    if not force_reload and _cached_permissions is not None:
        return copy.deepcopy(_cached_permissions)

    if not is_enterprise_runtime():
        cfg = _load_permissions_from_yaml()
        _cached_permissions = cfg
        _cache_source = "yaml"
        return copy.deepcopy(cfg)

    if _event_loop_is_running():
        if _cached_permissions is not None:
            return copy.deepcopy(_cached_permissions)
        cfg = _load_permissions_from_yaml()
        _cached_permissions = cfg
        _cache_source = "yaml_fallback"
        return copy.deepcopy(cfg)

    body = _run_async(_load_permissions_body_from_db())
    if isinstance(body, dict) and body:
        _cached_permissions = body
        _cache_source = "gateway_db"
        return copy.deepcopy(body)

    cfg = _load_permissions_from_yaml()
    _cached_permissions = cfg
    _cache_source = "yaml_fallback"
    return copy.deepcopy(cfg)


def apply_permissions_config_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """将 WS payload / 冷启动读库结果应用到本进程缓存（同 logging 热更新模式）。"""
    clear_permissions_config_cache()

    if not payload or payload.get("op") == "delete":
        effective = _load_permissions_from_yaml()
        _set_cache(effective, "yaml_fallback")
    elif isinstance(payload.get("body"), dict):
        effective = copy.deepcopy(payload["body"])
        _set_cache(effective, "gateway_db")
    else:
        effective = _load_permissions_from_yaml()
        _set_cache(effective, "yaml_fallback")

    return copy.deepcopy(effective)


async def reload_permissions_from_gateway_db() -> dict[str, Any]:
    """冷启动：从 Gateway 库加载 ``permissions_config`` 并刷新缓存。"""
    if not is_enterprise_runtime():
        return apply_permissions_config_payload({"op": "delete"})
    try:
        body = await _load_permissions_body_from_db()
        if isinstance(body, dict) and body:
            return apply_permissions_config_payload({"body": body})
        return apply_permissions_config_payload({"op": "delete"})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[permissions_config] permissions_config read failed: %s",
            exc,
            exc_info=True,
        )
        return apply_permissions_config_payload({"op": "delete"})


def persist_permissions_mutate(
    mutate_fn: Callable[[dict[str, Any]], None],
    *,
    source: str = "runtime_persist",
) -> dict[str, Any]:
    """变更 permissions 并持久化（企业版写 Gateway DB，否则写 config.yaml）。"""
    permissions = get_effective_permissions_config()
    if not isinstance(permissions, dict):
        permissions = {}
    else:
        permissions = copy.deepcopy(permissions)

    mutate_fn(permissions)

    if is_enterprise_runtime() and _gateway_db_available():
        if _event_loop_is_running():
            loop = asyncio.get_running_loop()
            task = loop.create_task(
                _upsert_permissions_config_to_db(permissions, source=source),
            )

            def _log_persist_error(done: asyncio.Task[Any]) -> None:
                if done.cancelled():
                    return
                exc = done.exception()
                if exc is not None:
                    logger.warning(
                        "[permissions_config] async permissions persist failed",
                        exc_info=exc,
                    )

            task.add_done_callback(_log_persist_error)
        else:
            _run_async(_upsert_permissions_config_to_db(permissions, source=source))
        _set_cache(permissions, "gateway_db")
    else:
        _persist_permissions_to_yaml(permissions)

    return permissions


def _persist_permissions_to_yaml(permissions: dict[str, Any]) -> None:
    from jiuwenswarm.common.config import (
        CONFIG_YAML_PATH,
        dump_yaml_round_trip,
        load_yaml_round_trip,
    )

    data = load_yaml_round_trip(CONFIG_YAML_PATH)
    data["permissions"] = permissions
    dump_yaml_round_trip(CONFIG_YAML_PATH, data)
    clear_permissions_config_cache()


def _gateway_db_available() -> bool:
    try:
        from jiuwenswarm.server.runtime.enterprise_config import gateway_db

        return bool(gateway_db.resolve_gateway_db_path())
    except Exception:  # noqa: BLE001
        return False


async def _load_permissions_body_from_db() -> dict[str, Any] | None:
    from jiuwenswarm.server.runtime.enterprise_config import gateway_db

    jid = gateway_db.resolve_jiuwenclaw_id()
    if not jid:
        return None

    rows = await gateway_db.list_records(
        PERMISSIONS_CONFIG_TABLE,
        filters={"jiuwenclaw_id": jid},
    )
    row = rows[0] if rows else None
    if row is None:
        return None
    body = row.get("body")
    if isinstance(body, dict) and body:
        return copy.deepcopy(body)
    return None


async def _upsert_permissions_config_to_db(
    body: dict[str, Any],
    *,
    source: str = "runtime_persist",
) -> None:
    from jiuwenswarm.server.runtime.enterprise_config import gateway_db

    jid = gateway_db.resolve_jiuwenclaw_id()
    if not jid:
        raise ValueError("JIUWENCLAW_ID is required for enterprise permissions persist")

    db_path = gateway_db.resolve_gateway_db_path()
    if not db_path:
        raise RuntimeError("gateway db not available")

    now = _utc_now().isoformat()
    body_json = json.dumps(body, ensure_ascii=False)

    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute(
            f"SELECT id, revision FROM {PERMISSIONS_CONFIG_TABLE} WHERE jiuwenclaw_id = ?",
            (jid,),
        ) as cursor:
            existing = await cursor.fetchone()
        if existing is not None:
            revision = int(existing[1] or 1) + 1
            await conn.execute(
                f"""
                UPDATE {PERMISSIONS_CONFIG_TABLE}
                SET body = ?, source = ?, revision = ?, updated_at = ?
                WHERE jiuwenclaw_id = ?
                """,
                (body_json, source, revision, now, jid),
            )
        else:
            await conn.execute(
                f"""
                INSERT INTO {PERMISSIONS_CONFIG_TABLE}
                (jiuwenclaw_id, body, source, revision, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                (jid, body_json, source, now, now),
            )
        await conn.commit()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _event_loop_is_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _run_async(awaitable: Any) -> Any:
    """仅在无运行中 event loop 的同步上下文中执行 DB 协程。"""
    if _event_loop_is_running():
        raise RuntimeError(
            "permissions config async DB operation invoked while event loop is running",
        )
    return asyncio.run(awaitable)
