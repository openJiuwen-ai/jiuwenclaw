# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway manager_ws_client 扩展。"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from jiuwenclaw.config import get_config
from jiuwenclaw.extensions.registry import ExtensionRegistry
from jiuwenclaw.extensions.sdk.base import BaseExtension
from jiuwenclaw.extensions.types import ExtensionConfig
from jiuwenclaw.schema.hook_event import GatewayHookEvents

from .ws_client.manager_ws_client import ManagerWsClient

logger = logging.getLogger(__name__)

DEFAULT_WS_URL = "ws://127.0.0.1:8766"

_client: ManagerWsClient | None = None
_connect_task: asyncio.Task[None] | None = None


def _resolve_enabled_and_uri() -> tuple[bool, str]:
    cfg = get_config()
    ext_cfg = cfg.get("extensions") if isinstance(cfg, dict) else {}
    ws_cfg = ext_cfg.get("manager_ws_client") if isinstance(ext_cfg, dict) else {}
    if not isinstance(ws_cfg, dict):
        ws_cfg = {}

    raw_en = ws_cfg.get("enabled", True)
    if isinstance(raw_en, bool):
        enabled = raw_en
    elif isinstance(raw_en, str):
        enabled = raw_en.strip().lower() in ("true", "1", "yes", "on")
    else:
        enabled = bool(raw_en)

    uri = (
        str(ws_cfg.get("ws_url") or "").strip()
        or os.getenv("MANAGER_WS_URL", "").strip()
        or DEFAULT_WS_URL
    )
    return enabled, uri


def _resolve_ws_service_identity() -> tuple[str, str]:
    """解析本进程的 WS 注册身份（与 provision 的 gw-/as- service_id 对齐）。"""
    service_id = os.getenv("JIUWENCLAW_SERVICE_ID", "gateway-1").strip() or "gateway-1"
    explicit = os.getenv("JIUWENCLAW_SERVICE_TYPE", "").strip().lower()
    if explicit:
        return explicit, service_id
    if service_id.startswith("as-"):
        return "agent_server", service_id
    if service_id.startswith("gw-"):
        return "gateway", service_id
    return "gateway", service_id


def _is_gateway_config_consumer(service_type: str) -> bool:
    """仅 Gateway 进程应将 config.push 写入 Gateway 本地库（如 model_template）。"""
    return service_type == "gateway"


async def _on_config_push(revision: str, config: dict[str, Any]) -> dict[str, Any] | None:
    from .ws_client.manager_ws_client_router import apply_config_push

    logger.info(
        "[ManagerWsClient] config.push revision=%s keys=%s",
        revision,
        list(config.keys()),
    )
    return await apply_config_push(config)


class ManagerWsClientExtension(BaseExtension):
    """在 Gateway 启动后连接 Claw Manager manager_ws_server。"""

    def __init__(self, client: ManagerWsClient) -> None:
        self._client = client

    async def initialize(self, config: ExtensionConfig) -> None:
        # distributed 模式：STANDBY 默认不连 Manager；由 app_gateway 在选主成功后
        # 通过 start_manager_ws_connect() 触发连接，避免备实例与 Manager 建立无意义会话。
        if _is_distributed_deployment():
            logger.info(
                "[ManagerWsClient] distributed deployment: defer connect until elected PRIMARY"
            )
            return
        _schedule_manager_ws_connect()

    async def shutdown(self) -> None:
        await stop_manager_ws_connect()

    def get_client(self) -> ManagerWsClient:
        return self._client


def _is_distributed_deployment() -> bool:
    gw_cfg = get_config().get("gateway") or {}
    mode = str(gw_cfg.get("deployment_mode", "standalone")).strip().lower()
    return mode != "standalone"


def start_manager_ws_connect() -> None:
    """外部入口：触发连接 Manager（幂等）。distributed 模式由 LeaderElection 选主后调用。"""
    _schedule_manager_ws_connect()


async def stop_manager_ws_connect() -> None:
    """外部入口：取消连接任务并断开 client（幂等）。distributed 模式失主时调用。"""
    global _connect_task
    if _connect_task is not None and not _connect_task.done():
        _connect_task.cancel()
        try:
            await _connect_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ManagerWsClient] connect task await error: %s", exc)
        _connect_task = None
    if _client is not None:
        try:
            await _client.disconnect()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ManagerWsClient] disconnect error: %s", exc)


def _schedule_manager_ws_connect() -> None:
    """启动（或复用）连接 Manager WS 的后台任务；失败时 client 内会按间隔重试。"""
    global _connect_task
    if _client is None:
        return

    enabled, uri = _resolve_enabled_and_uri()
    if not enabled:
        logger.info("[ManagerWsClient] disabled by config")
        return
    if not uri:
        logger.warning("[ManagerWsClient] ws url empty, skip connect")
        return

    if _connect_task is not None and not _connect_task.done():
        return

    async def _connect() -> None:
        try:
            await _client.connect(uri)
            logger.info("[ManagerWsClient] connect task started uri=%s", uri)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[ManagerWsClient] connect failed: %s", exc)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("[ManagerWsClient] no running event loop, skip connect")
        return

    _connect_task = loop.create_task(_connect(), name="manager-ws-client-connect")


async def _on_web_channel_created(ctx: Any) -> None:
    _schedule_manager_ws_connect()


async def register_extensions(registry: ExtensionRegistry) -> list[ManagerWsClientExtension]:
    global _client

    service_type, service_id = _resolve_ws_service_identity()
    if not _is_gateway_config_consumer(service_type):
        logger.info(
            "[ManagerWsClient] skip load on service_type=%s service_id=%s "
            "(config.push DB sync is gateway-only)",
            service_type,
            service_id,
        )
        return []

    _client = ManagerWsClient(
        service_type=service_type,
        service_id=service_id,
        on_config_push=_on_config_push,
    )
    ext = ManagerWsClientExtension(_client)
    await ext.initialize(registry.config)

    registry.register(
        GatewayHookEvents.WEB_CHANNEL_CREATED,
        _on_web_channel_created,
        priority=400,
    )
    return [ext]
