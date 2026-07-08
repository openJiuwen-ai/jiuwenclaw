# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""从 Gateway ``channel_config`` 表（manager_ws_client ``DBHandler``）构建运行时 ``channels``。

企业实例在启用 overlay 时 **仅以 DB 为准**：``status=active`` 行重建 ``channels.*``，
不合并、不保留 ``config.yaml`` 中的 IM 通道段；不写回 yaml。

热加载：WebSocket 写库后携带 ``ChannelConfigChange`` 增量 patch 内存 ``channels``，避免每次
全表读库；冷启动经 ``channel_config_db`` 全量读库（与 WS 写库同栈）。
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from jiuwenclaw.deployment_mode import (
    VALID_DEPLOYMENT_MODES,
    channel_config_overlay_default,
)
from jiuwenclaw.gateway.channel_config_db import load_active_channel_config_rows
from jiuwenclaw.utils import logger

ChannelChangeOp = Literal["upsert", "remove"]

# 与 app_gateway._apply_channel_config 飞书单 bot 顶格字段一致，用于识别多 bot 子段。
_SINGLE_BOT_FIELD_KEYS = frozenset(
    {
        "app_id",
        "app_secret",
        "encrypt_key",
        "verification_token",
        "allow_from",
        "enable_streaming",
        "chat_id",
        "enabled",
        "send_file_allowed",
        "group_digital_avatar",
        "my_user_id",
        "my_open_id",
        "bot_name",
        "enable_memory",
        "message_merge_window_ms",
        "last_chat_id",
        "last_open_id",
        "last_message_id",
    }
)

_ReloadCallback = Callable[["ChannelConfigChange | None"], Awaitable[None]]
_reload_callback: _ReloadCallback | None = None
_reload_lock = asyncio.Lock()


@dataclass(frozen=True)
class ChannelConfigChange:
    """单次 ``channel_config`` 表变更（由 manager_ws_client 写库后传入）。"""

    op: ChannelChangeOp
    row: dict[str, Any]

    @classmethod
    def upsert(cls, row: dict[str, Any]) -> ChannelConfigChange:
        return cls("upsert", dict(row))

    @classmethod
    def remove(cls, row: dict[str, Any]) -> ChannelConfigChange:
        return cls("remove", dict(row))


async def register_channel_config_reload(callback: _ReloadCallback | None) -> None:
    """注册 Channel 热加载回调（在 ``app_gateway`` 启动时调用一次）。"""
    global _reload_callback
    async with _reload_lock:
        _reload_callback = callback


async def trigger_channel_config_reload(
    change: ChannelConfigChange | None = None,
) -> None:
    """在 ``channel_config`` 写库变更后调用；可携带增量变更，避免全表读库。"""
    if not channel_config_overlay_enabled():
        return
    async with _reload_lock:
        callback = _reload_callback
        if callback is None:
            return
        try:
            await callback(change)
        except Exception:  # noqa: BLE001
            logger.exception("[channel_config_overlay] reload callback failed")


def _deployment_mode_from_env() -> str | None:
    raw = os.getenv("DEPLOYMENT_MODE", "").strip().lower()
    if raw in VALID_DEPLOYMENT_MODES:
        return raw
    return None


def _gateway_deployment_mode() -> str:
    """读取 ``gateway.deployment_mode``（``standalone`` | ``active-standby`` | ``distributed``）。"""
    mode = ""
    try:
        from jiuwenclaw.config import get_config

        cfg = get_config()
        gw = cfg.get("gateway") if isinstance(cfg, dict) else {}
        gw = gw if isinstance(gw, dict) else {}
        mode = str(gw.get("deployment_mode") or "").strip().lower()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[channel_config_overlay] failed to read gateway.deployment_mode from config: %s",
            exc,
            exc_info=True,
        )
        env_mode = _deployment_mode_from_env()
        if env_mode is not None:
            logger.info(
                "[channel_config_overlay] using DEPLOYMENT_MODE env fallback: %s",
                env_mode,
            )
            return env_mode
        return "standalone"

    if not mode:
        mode = _deployment_mode_from_env() or "standalone"
    if mode not in VALID_DEPLOYMENT_MODES:
        logger.warning(
            "[channel_config_overlay] invalid gateway.deployment_mode=%r; defaulting to standalone",
            mode,
        )
        return "standalone"
    return mode


def channel_config_overlay_enabled() -> bool:
    """是否用 ``channel_config`` 表作为运行时 ``channels`` 的唯一来源。

    与 ``gateway.deployment_mode`` 对齐：
    - ``standalone`` / ``distributed``：仅 ``config.yaml`` 的 ``channels``；
    - ``active-standby``：企业/K8s 部署，仅 DB（``channel_config`` active 行）。
    """
    return channel_config_overlay_default(_gateway_deployment_mode())


async def fetch_active_channel_config_rows() -> list[dict[str, Any]]:
    """读取 ``channel_config`` 表中所有 active 行（冷启动全量，``DBHandler``）。"""
    return await load_active_channel_config_rows()


def _extract_channel_payload(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("config")
    if isinstance(raw, dict):
        payload = copy.deepcopy(raw)
    elif isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            payload = dict(parsed) if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            payload = {}
    else:
        payload = {}

    if row.get("channel_name"):
        payload.setdefault("channel_name", row["channel_name"])
    payload.setdefault("enabled", True)
    return payload


def _subsection_key(row: dict[str, Any]) -> str | None:
    bot_id = str(row.get("bot_id") or "").strip()
    channel_id = str(row.get("channel_id") or "").strip()
    channel_type = str(row.get("channel_type") or "").strip().lower()
    if bot_id and bot_id.lower() != channel_type:
        return bot_id
    if channel_id and channel_id.lower() != channel_type:
        return channel_id
    return None


def _is_single_bot_top_level(conf: dict[str, Any]) -> bool:
    return any(k in conf for k in _SINGLE_BOT_FIELD_KEYS)


def _row_remove_keys(row: dict[str, Any]) -> tuple[str, str | None]:
    ctype = str(row.get("channel_type") or "").strip().lower()
    subkey = _subsection_key(row)
    return ctype, subkey


def apply_channel_change_to_runtime(
    current_channels: dict[str, Any] | None,
    change: ChannelConfigChange,
) -> dict[str, Any]:
    """在内存 ``channels`` 上应用单次 DB 变更（不读库）。"""
    channels = copy.deepcopy(current_channels) if isinstance(current_channels, dict) else {}
    row = change.row
    ctype, subkey = _row_remove_keys(row)
    if not ctype:
        logger.warning("[channel_config_overlay] change missing channel_type: %r", row)
        return channels

    if change.op == "upsert":
        return _upsert_row(channels, ctype, subkey, row)

    return _remove_row(channels, ctype, subkey, row)


def _upsert_row(
    channels: dict[str, Any],
    ctype: str,
    subkey: str | None,
    row: dict[str, Any],
) -> dict[str, Any]:
    payload = _extract_channel_payload(row)
    if subkey is None:
        channels[ctype] = payload
        return channels

    existing = channels.get(ctype)
    if not isinstance(existing, dict):
        channels[ctype] = {subkey: payload}
        return channels

    if _is_single_bot_top_level(existing):
        legacy_key = str(row.get("channel_type") or ctype).strip() or ctype
        section = {legacy_key: copy.deepcopy(existing), subkey: payload}
        channels[ctype] = section
        return channels

    section = copy.deepcopy(existing)
    section[subkey] = payload
    channels[ctype] = section
    return channels


def _remove_row(
    channels: dict[str, Any],
    ctype: str,
    subkey: str | None,
    row: dict[str, Any],
) -> dict[str, Any]:
    existing = channels.get(ctype)
    if not isinstance(existing, dict):
        channels.pop(ctype, None)
        return channels

    if subkey is None or _is_single_bot_top_level(existing):
        channels.pop(ctype, None)
        return channels

    section = copy.deepcopy(existing)
    section.pop(subkey, None)
    cid = str(row.get("channel_id") or "").strip()
    if cid:
        section.pop(cid, None)

    if not section:
        channels.pop(ctype, None)
    else:
        channels[ctype] = section
    return channels


def build_channels_from_db_rows(db_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """仅从 DB active 行构建 ``channels``（冷启动全量）。"""
    if not db_rows:
        return {}

    channels: dict[str, Any] = {}
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in db_rows:
        ctype = str(row.get("channel_type") or "").strip().lower()
        if not ctype:
            continue
        by_type[ctype].append(row)

    for ctype, type_rows in by_type.items():
        if len(type_rows) == 1 and _subsection_key(type_rows[0]) is None:
            channels[ctype] = _extract_channel_payload(type_rows[0])
            continue

        section: dict[str, Any] = {}
        for r in type_rows:
            key = _subsection_key(r) or str(r.get("channel_id") or "").strip()
            if not key:
                logger.warning(
                    "[channel_config_overlay] skip row without subsection key: %r",
                    r,
                )
                continue
            section[key] = _extract_channel_payload(r)
        channels[ctype] = section

    return channels


def merge_channels_with_db(
    base_channels: dict[str, Any] | None,
    db_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    _ = base_channels
    return build_channels_from_db_rows(db_rows)


async def apply_channel_config_db_overlay(
    base_channels: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """冷启动：全量读 DB 构建 ``channels``。"""
    if not channel_config_overlay_enabled():
        return (dict(base_channels) if isinstance(base_channels, dict) else {}), False

    rows = await fetch_active_channel_config_rows()
    channels = build_channels_from_db_rows(rows)
    if not rows:
        logger.info(
            "[channel_config_overlay] enabled; no active channel_config rows, runtime channels empty"
        )
    else:
        logger.info(
            "[channel_config_overlay] built channels from %d active channel_config row(s) (db-only)",
            len(rows),
        )
    return channels, True
