# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Permissions 配置加载：企业版 GDB 优先，否则回落 config.yaml。

冷启动 / 热更新入口在本模块，不放在 ``utils.py``。
"""

from __future__ import annotations

import asyncio
import contextvars
import copy
import logging
import os
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)

PERMISSIONS_CONFIG_TABLE = "permissions_config"

PersistScope = Literal["session", "base"]

_cached_permissions: dict[str, Any] | None = None
_cache_source: str | None = None
_session_overlays: dict[str, dict[str, Any]] = {}

PERMISSIONS_SESSION_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "jiuwenclaw_permissions_session_id",
    default=None,
)


def is_enterprise_runtime() -> bool:
    return bool(os.getenv("AGENT_RUNTIME", "").strip())


def setup_permissions_session_scope(session_id: str | None) -> contextvars.Token:
    """绑定当前 asyncio Task 的 permissions 会话 scope（供 overlay 读写）。"""
    normalized = (session_id or "").strip() or None
    return PERMISSIONS_SESSION_ID.set(normalized)


def reset_permissions_session_scope(token: contextvars.Token) -> None:
    PERMISSIONS_SESSION_ID.reset(token)


def get_permissions_session_id() -> str | None:
    return PERMISSIONS_SESSION_ID.get()


def clear_permissions_config_cache() -> None:
    global _cached_permissions, _cache_source
    _cached_permissions = None
    _cache_source = None


def clear_session_permissions_overlay(session_id: str | None = None) -> None:
    """清除企业版会话级 runtime overlay（可选：单会话或全部）。"""
    if session_id is None:
        _session_overlays.clear()
        return
    normalized = session_id.strip()
    if normalized:
        _session_overlays.pop(normalized, None)


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


def _resolve_session_id(session_id: str | None = None) -> str | None:
    if session_id is not None:
        normalized = session_id.strip()
        return normalized or None
    ctx = get_permissions_session_id()
    if ctx:
        return ctx.strip() or None
    return None


def _approval_override_fingerprint(item: dict[str, Any]) -> tuple[str, str]:
    pattern = str(item.get("pattern") or "").strip()
    action = str(item.get("action") or "").strip().lower()
    return pattern, action


def _deep_merge_file_guard(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if key == "global" and isinstance(value, dict):
            global_dst = dst.setdefault("global", {})
            if not isinstance(global_dst, dict):
                global_dst = {}
                dst["global"] = global_dst
            for path, rules in value.items():
                if path in global_dst and isinstance(global_dst[path], dict) and isinstance(rules, dict):
                    global_dst[path] = {**global_dst[path], **copy.deepcopy(rules)}
                else:
                    global_dst[path] = copy.deepcopy(rules)
            continue
        if key == "trusted_exec_directory" and isinstance(value, list):
            existing = dst.setdefault("trusted_exec_directory", [])
            if not isinstance(existing, list):
                existing = []
                dst["trusted_exec_directory"] = existing
            seen = {str(x) for x in existing}
            for item in value:
                normalized = str(item)
                if normalized not in seen:
                    existing.append(copy.deepcopy(item))
                    seen.add(normalized)
            continue
        if key in dst and isinstance(dst[key], dict) and isinstance(value, dict):
            _deep_merge_file_guard(dst[key], value)
        else:
            dst[key] = copy.deepcopy(value)


def _file_guard_delta(base_fg: dict[str, Any], effective_fg: dict[str, Any]) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    base_global = base_fg.get("global") if isinstance(base_fg.get("global"), dict) else {}
    eff_global = effective_fg.get("global") if isinstance(effective_fg.get("global"), dict) else {}
    global_delta: dict[str, Any] = {}
    for path, rules in eff_global.items():
        base_rules = base_global.get(path)
        if base_rules != rules:
            global_delta[path] = copy.deepcopy(rules)
    if global_delta:
        delta["global"] = global_delta

    base_ted_raw = base_fg.get("trusted_exec_directory")
    base_ted = base_ted_raw if isinstance(base_ted_raw, list) else []
    eff_ted_raw = effective_fg.get("trusted_exec_directory")
    eff_ted = eff_ted_raw if isinstance(eff_ted_raw, list) else []
    base_seen = {str(x) for x in base_ted}
    ted_delta = [copy.deepcopy(x) for x in eff_ted if str(x) not in base_seen]
    if ted_delta:
        delta["trusted_exec_directory"] = ted_delta

    for key in ("workspace", "tool_bindings"):
        base_val = base_fg.get(key)
        eff_val = effective_fg.get(key)
        if eff_val is not None and eff_val != base_val:
            delta[key] = copy.deepcopy(eff_val)
    return delta


def _merge_permissions_config(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    if not overlay:
        return copy.deepcopy(base)

    merged = copy.deepcopy(base)

    overlay_tools = overlay.get("tools")
    if isinstance(overlay_tools, dict):
        tools = merged.get("tools")
        if not isinstance(tools, dict):
            tools = {}
            merged["tools"] = tools
        tools.update(copy.deepcopy(overlay_tools))

    overlay_overrides = overlay.get("approval_overrides")
    if isinstance(overlay_overrides, list):
        base_list = list(merged.get("approval_overrides") or [])
        existing = {
            _approval_override_fingerprint(item)
            for item in base_list
            if isinstance(item, dict)
        }
        for item in overlay_overrides:
            if not isinstance(item, dict):
                continue
            fingerprint = _approval_override_fingerprint(item)
            if fingerprint in existing:
                continue
            base_list.append(copy.deepcopy(item))
            existing.add(fingerprint)
        merged["approval_overrides"] = base_list

    overlay_fg = overlay.get("file_guard")
    if isinstance(overlay_fg, dict):
        fg = merged.get("file_guard")
        if not isinstance(fg, dict):
            fg = {}
            merged["file_guard"] = fg
        _deep_merge_file_guard(fg, overlay_fg)

    return merged


def _extract_session_overlay(base: dict[str, Any], effective: dict[str, Any]) -> dict[str, Any]:
    overlay: dict[str, Any] = {}

    base_tools = base.get("tools") if isinstance(base.get("tools"), dict) else {}
    eff_tools = effective.get("tools") if isinstance(effective.get("tools"), dict) else {}
    tool_delta = {k: v for k, v in eff_tools.items() if base_tools.get(k) != v}
    if tool_delta:
        overlay["tools"] = copy.deepcopy(tool_delta)

    base_overrides = [
        item for item in (base.get("approval_overrides") or []) if isinstance(item, dict)
    ]
    eff_overrides = [
        item for item in (effective.get("approval_overrides") or []) if isinstance(item, dict)
    ]
    base_fps = {_approval_override_fingerprint(item) for item in base_overrides}
    runtime_overrides = [
        copy.deepcopy(item)
        for item in eff_overrides
        if _approval_override_fingerprint(item) not in base_fps
    ]
    if runtime_overrides:
        overlay["approval_overrides"] = runtime_overrides

    base_fg = base.get("file_guard") if isinstance(base.get("file_guard"), dict) else {}
    eff_fg = effective.get("file_guard") if isinstance(effective.get("file_guard"), dict) else {}
    fg_delta = _file_guard_delta(base_fg, eff_fg)
    if fg_delta:
        overlay["file_guard"] = fg_delta

    return overlay


def merge_session_permissions_overlay(
    base_config: dict[str, Any],
    session_id: str | None = None,
) -> dict[str, Any]:
    """将 base 配置与会话 overlay 合并（供 PermissionEngine 判定）。"""
    if not is_enterprise_runtime():
        return copy.deepcopy(base_config)
    sid = _resolve_session_id(session_id)
    if not sid:
        return copy.deepcopy(base_config)
    overlay = _session_overlays.get(sid)
    if not overlay:
        return copy.deepcopy(base_config)
    return _merge_permissions_config(base_config, overlay)


def get_base_permissions_config(*, force_reload: bool = False) -> dict[str, Any]:
    """返回 base ``permissions`` 段（不含企业版会话 overlay）。"""
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


def get_effective_permissions_config(
    *,
    force_reload: bool = False,
    session_id: str | None = None,
) -> dict[str, Any]:
    """返回生效的 ``permissions`` 段（企业版：base + 会话 overlay；其他：YAML/base）。"""
    base = get_base_permissions_config(force_reload=force_reload)
    if not is_enterprise_runtime():
        return base
    return merge_session_permissions_overlay(base, session_id=session_id)


def apply_permissions_config_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """将 WS payload / 冷启动读库结果应用到本进程 base 缓存与 ``PermissionEngine``。

    与 ``apply_logging_config_payload`` 同模式：只做内存热更新，不在此路径二次读 GDB。
    不清理各会话 runtime overlay。
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
    session_id: str | None = None,
    persist_scope: PersistScope = "session",
) -> dict[str, Any]:
    """变更 permissions 并持久化。

    - 标准版：写 ``config.yaml``。
    - 企业版 + ``persist_scope='session'``：仅更新指定会话的内存 overlay。
    - 企业版 + ``persist_scope='base'``：更新进程内 base 缓存（如 Web UI / CLI 管理路径）。
    """
    if is_enterprise_runtime() and persist_scope == "session":
        sid = _resolve_session_id(session_id)
        if not sid:
            logger.warning(
                "[permissions_config] session persist skipped: no session_id",
            )
            return get_effective_permissions_config()

        base = get_base_permissions_config()
        overlay = _session_overlays.get(sid, {})
        effective = _merge_permissions_config(base, overlay)
        mutate_fn(effective)
        _session_overlays[sid] = _extract_session_overlay(base, effective)
        logger.info(
            "[permissions_config] session overlay updated session_id=%s",
            sid,
        )
        return copy.deepcopy(effective)

    permissions = get_base_permissions_config()
    if not isinstance(permissions, dict):
        permissions = {}
    else:
        permissions = copy.deepcopy(permissions)

    mutate_fn(permissions)

    if is_enterprise_runtime():
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


async def _load_permissions_body_from_db() -> dict[str, Any] | None:
    jid = _jiuwenclaw_id()
    if not jid:
        return None

    handler = await _ensure_db_handler()
    row = await handler.get(PERMISSIONS_CONFIG_TABLE, {"jiuwenclaw_id": jid})
    return _permissions_config_row_to_body(row)


async def _ensure_db_handler():
    from jiuwenclaw.infrastructure.module_importer import import_manager_ws_client_module

    db_mod = import_manager_ws_client_module("infrastructure.db")
    return await db_mod.ensure_db_handler(log_prefix="permissions_config")


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
