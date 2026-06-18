# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Permissions 配置加载：企业版 GDB 优先，否则回落 config.yaml。

冷启动 / 热更新入口在本模块，不放在 ``utils.py``。
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable

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
    from jiuwenclaw.config import get_config

    raw = (get_config() or {}).get("permissions")
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    return {}


def _jiuwenclaw_id() -> str:
    return (os.getenv("JIUWENCLAW_ID") or "").strip()


def _permissions_config_row_to_body(obj: Any) -> dict[str, Any] | None:
    if obj is None:
        return None
    body = getattr(obj, "body", None)
    if isinstance(body, dict) and body:
        return copy.deepcopy(body)
    return None


def _set_cache(body: dict[str, Any], source: str) -> None:
    global _cached_permissions, _cache_source
    _cached_permissions = copy.deepcopy(body)
    _cache_source = source


def get_effective_permissions_config(*, force_reload: bool = False) -> dict[str, Any]:
    """返回生效的 ``permissions`` 段（企业版：GDB → YAML；其他：YAML）。"""
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
    """将 WS payload / 冷启动读库结果应用到本进程缓存与 ``PermissionEngine``。

    与 ``apply_logging_config_payload`` 同模式：只做内存热更新，不在此路径二次读 GDB。
    """
    from jiuwenclaw.agentserver.permissions.core import get_permission_engine

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

    try:
        get_permission_engine().update_config(effective)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[permissions_config] permission engine hot-reload failed",
            exc_info=True,
        )
    return copy.deepcopy(effective)


async def reload_permissions_from_gateway_db() -> dict[str, Any]:
    """冷启动：从 Gateway 库加载 ``permissions_config`` 并热更新引擎（同 logging 冷启动）。"""
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
    """变更 permissions 并持久化（企业版写 GDB，否则写 config.yaml）。"""
    permissions = get_effective_permissions_config()
    if not isinstance(permissions, dict):
        permissions = {}
    else:
        permissions = copy.deepcopy(permissions)

    mutate_fn(permissions)

    if is_enterprise_runtime() and _db_module_available():
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

    from jiuwenclaw.agentserver.permissions.core import get_permission_engine

    get_permission_engine().update_config(permissions)
    return permissions


def _persist_permissions_to_yaml(permissions: dict[str, Any]) -> None:
    from jiuwenclaw.config import (
        _current_config_yaml_path,
        _dump_yaml_round_trip,
        _load_yaml_round_trip,
    )

    data = _load_yaml_round_trip(_current_config_yaml_path())
    data["permissions"] = permissions
    _dump_yaml_round_trip(_current_config_yaml_path(), data)
    clear_permissions_config_cache()


def _db_module_available() -> bool:
    try:
        from jiuwenclaw.infrastructure.module_importer import import_manager_ws_client_module

        import_manager_ws_client_module("infrastructure.db")
        return True
    except Exception:  # noqa: BLE001
        return False


async def _load_permissions_body_from_db() -> dict[str, Any] | None:
    jid = _jiuwenclaw_id()
    if not jid:
        return None

    handler = await _ensure_db_handler()
    row = await handler.get(PERMISSIONS_CONFIG_TABLE, {"jiuwenclaw_id": jid})
    return _permissions_config_row_to_body(row)


async def _upsert_permissions_config_to_db(
    body: dict[str, Any],
    *,
    source: str = "runtime_persist",
) -> dict[str, Any] | None:
    jid = _jiuwenclaw_id()
    if not jid:
        raise ValueError("JIUWENCLAW_ID is required for enterprise permissions persist")

    handler = await _ensure_db_handler()
    now = _utc_now()
    existing = await handler.get(PERMISSIONS_CONFIG_TABLE, {"jiuwenclaw_id": jid})

    if existing is not None:
        update_data: dict[str, Any] = {
            "body": body,
            "source": source,
            "updated_at": now,
            "revision": int(getattr(existing, "revision", 1) or 1) + 1,
        }
        updated = await handler.update(
            PERMISSIONS_CONFIG_TABLE,
            {"jiuwenclaw_id": jid},
            update_data,
        )
        return _row_to_dict(updated) if updated is not None else None

    row_data = {
        "jiuwenclaw_id": jid,
        "body": body,
        "source": source,
        "revision": 1,
        "created_at": now,
        "updated_at": now,
    }
    created = await handler.create(PERMISSIONS_CONFIG_TABLE, row_data)
    return _row_to_dict(created) if created is not None else None


async def _delete_permissions_config_from_db() -> None:
    jid = _jiuwenclaw_id()
    if not jid:
        return
    handler = await _ensure_db_handler()
    await handler.delete(PERMISSIONS_CONFIG_TABLE, {"jiuwenclaw_id": jid})


def _row_to_dict(obj: Any) -> dict[str, Any]:
    body = getattr(obj, "body", None)
    return {
        "id": getattr(obj, "id", None),
        "jiuwenclaw_id": getattr(obj, "jiuwenclaw_id", None),
        "body": dict(body) if isinstance(body, dict) else body,
        "source": getattr(obj, "source", None),
        "revision": getattr(obj, "revision", None),
        "created_at": getattr(obj, "created_at", None),
        "updated_at": getattr(obj, "updated_at", None),
    }


async def _ensure_db_handler():
    from jiuwenclaw.infrastructure.module_importer import import_manager_ws_client_module

    db_mod = import_manager_ws_client_module("infrastructure.db")
    return await db_mod.ensure_db_handler(log_prefix="permissions_config")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _event_loop_is_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _run_async(awaitable: Any) -> Any:
    """仅在无运行中 event loop 的同步上下文中执行 GDB 协程。"""
    if _event_loop_is_running():
        raise RuntimeError(
            "permissions config async DB operation invoked while event loop is running",
        )
    return asyncio.run(awaitable)
