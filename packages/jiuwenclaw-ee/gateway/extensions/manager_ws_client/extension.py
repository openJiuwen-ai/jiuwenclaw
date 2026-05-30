# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway manager_ws_client 扩展。"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from jiuwenclaw.config import get_config
from jiuwenclaw.extensions.registry import ExtensionRegistry
from jiuwenclaw.extensions.sdk.base import BaseExtension
from jiuwenclaw.extensions.types import ExtensionConfig
from jiuwenclaw.schema.hook_event import GatewayHookEvents

from .infrastructure.config import get_settings
from .routers.manager_ws_client_router import apply_config_push
from .ws_client.manager_ws_client import ManagerWsClient

logger = logging.getLogger(__name__)

_extension: ManagerWsClientExtension | None = None


class ManagerWsClientExtension(BaseExtension):
    """在 Gateway 启动后连接 Claw Manager manager_ws_server。"""

    def __init__(self, client: ManagerWsClient) -> None:
        self._client = client
        self._connect_task: asyncio.Task[None] | None = None

    @staticmethod
    def _is_distributed_deployment() -> bool:
        gw_cfg = get_config().get("gateway") or {}
        mode = str(gw_cfg.get("deployment_mode", "standalone")).strip().lower()
        return mode != "standalone"

    async def initialize(self, config: ExtensionConfig) -> None:
        # distributed 模式：STANDBY 默认不连 Manager；由 app_gateway 在选主成功后
        # 通过 start_manager_ws_connect() 触发连接，避免备实例与 Manager 建立无意义会话。
        if self._is_distributed_deployment():
            logger.info(
                "[ManagerWsClient] distributed deployment: defer connect until elected PRIMARY"
            )
            return
        self.start_manager_ws_connect()

    async def shutdown(self) -> None:
        await self.stop_manager_ws_connect()

    def get_client(self) -> ManagerWsClient:
        return self._client

    async def stop_manager_ws_connect(self) -> None:
        """取消连接任务并断开 client（幂等）。distributed 模式失主时调用。"""
        task = self._connect_task
        if task is not None:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            else:
                task_exc = task.exception()
                if task_exc is not None:
                    logger.warning(
                        "[ManagerWsClient] connect task finished with error: %s",
                        task_exc,
                    )
            self._connect_task = None
        await self._client.disconnect()

    def start_manager_ws_connect(self, _ctx: object = None) -> None:
        """触发连接 Manager（幂等）。standalone 启动 / WEB_CHANNEL_CREATED / 选主成功后调用。"""
        cfg = get_settings()
        if not cfg.gateway_manager_ws_client_enabled:
            logger.info("[ManagerWsClient] disabled by config")
            return
        uri = cfg.gateway_manager_ws_url.strip()
        if not uri:
            logger.warning("[ManagerWsClient] ws url empty, skip connect")
            return

        if self._connect_task is not None and not self._connect_task.done():
            return

        client = self._client

        async def _connect() -> None:
            await client.connect(uri)
            logger.info("[ManagerWsClient] connect task started uri=%s", uri)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("[ManagerWsClient] no running event loop, skip connect")
            return

        self._connect_task = loop.create_task(_connect(), name="manager-ws-client-connect")


def start_manager_ws_connect(_ctx: object = None) -> None:
    """模块入口：供 app_gateway 选主回调与 WEB_CHANNEL_CREATED 钩子调用。"""
    if _extension is not None:
        _extension.start_manager_ws_connect(_ctx)


async def stop_manager_ws_connect() -> None:
    """模块入口：供 app_gateway 选主回调调用。"""
    if _extension is not None:
        await _extension.stop_manager_ws_connect()


async def register_extensions(registry: ExtensionRegistry) -> list[ManagerWsClientExtension]:
    global _extension

    client = ManagerWsClient(
        service_type="gateway",
        on_config_push=apply_config_push,
    )
    ext = ManagerWsClientExtension(client)
    _extension = ext
    await ext.initialize(registry.config)

    registry.register(
        GatewayHookEvents.WEB_CHANNEL_CREATED,
        start_manager_ws_connect,
        priority=400,
    )
    return [ext]
