# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway manager_ws_client：连接 Claw Manager 配置下发 WebSocket。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

from ..core.enterprise_config.gateway_db import GatewayDb
from ..infrastructure.utils import get_jiuwenclaw_id, set_jiuwenclaw_id
from .protocol import (
    EVENT_CONNECTION_ACK,
    EVENT_REGISTER_ACK,
    FRAME_TYPE_CONFIG_PUSH,
    FRAME_TYPE_ERROR,
    FRAME_TYPE_EVENT,
    build_config_ack,
    build_heartbeat,
    build_register,
)

logger = logging.getLogger(__name__)

ManagerWsConfigPushHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


def _build_ws_origin(uri: str) -> str | None:
    try:
        parsed = urlsplit(uri)
    except ValueError:
        return None
    if not parsed.netloc:
        return None
    scheme = "https" if parsed.scheme == "wss" else "http"
    return f"{scheme}://{parsed.netloc}"


class ManagerWsClient:
    """连接 Claw Manager manager_ws_server，接收 config.push 并回 ack。"""

    def __init__(
        self,
        *,
        service_type: str = "gateway",
        ping_interval: float | None = 30.0,
        ping_timeout: float | None = 300.0,
        heartbeat_interval_seconds: float = 10.0,
        on_config_push: ManagerWsConfigPushHandler | None = None,
    ) -> None:
        self._service_type = service_type
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._heartbeat_interval_seconds = max(1.0, float(heartbeat_interval_seconds))
        self._on_config_push = on_config_push
        self._uri: str | None = None
        self._ws: Any = None
        self._running = False
        self._session_task: asyncio.Task[None] | None = None
        self._ready = False

    @property
    def jiuwenclaw_id(self) -> str | None:
        return get_jiuwenclaw_id()

    @property
    def ready(self) -> bool:
        return self._ready and self._ws is not None

    def set_config_push_handler(self, handler: ManagerWsConfigPushHandler | None) -> None:
        self._on_config_push = handler

    async def connect(self, uri: str) -> None:
        # initialize 与 WEB_CHANNEL_CREATED 都会 schedule connect；勿重复 disconnect，
        # 否则会清空 JIUWENCLAW_ID 并以无 id 的 register 在 Manager 侧再建一条 instance。
        if (
            self._uri == uri
            and self._session_task is not None
            and not self._session_task.done()
        ):
            return
        if self._session_task is not None and not self._session_task.done():
            await self.disconnect()

        self._uri = uri
        self._running = True
        self._session_task = asyncio.create_task(
            self._session_loop(uri),
            name="manager-ws-client",
        )

    async def disconnect(self) -> None:
        self._running = False
        self._ready = False
        set_jiuwenclaw_id(None)
        GatewayDb.bind(None)
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("[ManagerWsClient] close error: %s", exc)
            self._ws = None
        if self._session_task is not None:
            self._session_task.cancel()
            try:
                await self._session_task
            except asyncio.CancelledError:
                pass
            self._session_task = None
        logger.info("[ManagerWsClient] disconnected")

    async def _session_loop(self, uri: str) -> None:
        retry_interval = 3.0
        while self._running:
            try:
                await self._connect_once(uri)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                jid_label = get_jiuwenclaw_id() or "unassigned"
                logger.warning(
                    "[ManagerWsClient] session ended jiuwenclaw_id=%s, retry in %ss: %s",
                    jid_label,
                    retry_interval,
                    exc,
                )
            self._ready = False
            set_jiuwenclaw_id(None)
            GatewayDb.bind(None)
            if self._ws is not None:
                try:
                    await self._ws.close()
                except Exception:  # noqa: BLE001
                    pass
                self._ws = None
            if not self._running:
                break
            await asyncio.sleep(retry_interval)

    async def _connect_once(self, uri: str) -> None:
        origin = _build_ws_origin(uri)
        try:
            from websockets.legacy.client import connect as legacy_connect

            connect_fn = legacy_connect
        except ImportError:
            import websockets

            connect_fn = websockets.connect

        jid = get_jiuwenclaw_id()
        if jid:
            GatewayDb.bind(jid)

        logger.info("[ManagerWsClient] connecting %s", uri)
        async with connect_fn(
            uri,
            origin=origin,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
            close_timeout=5.0,
        ) as ws:
            self._ws = ws
            if not await self._handshake(ws):
                raise RuntimeError("manager ws handshake failed")
            logger.info(
                "[ManagerWsClient] session active jiuwenclaw_id=%s uri=%s",
                get_jiuwenclaw_id(),
                uri,
            )
            await self._recv_loop(ws)

    async def _handshake(self, ws: Any) -> bool:
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        data = json.loads(raw)
        if (
            data.get("type") == FRAME_TYPE_EVENT
            and data.get("event") == EVENT_CONNECTION_ACK
        ):
            payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
            logger.info(
                "[ManagerWsClient] connection.ack manager_id=%s",
                payload.get("manager_id"),
            )
        else:
            logger.warning("[ManagerWsClient] unexpected first frame: %s", data.get("type"))

        reg = build_register(service_type=self._service_type)
        await ws.send(json.dumps(reg, ensure_ascii=False))

        raw_reg = await asyncio.wait_for(ws.recv(), timeout=10.0)
        reg_data = json.loads(raw_reg)
        if reg_data.get("type") == FRAME_TYPE_ERROR:
            payload = reg_data.get("payload") if isinstance(reg_data.get("payload"), dict) else {}
            raise RuntimeError(f"manager ws register rejected: {payload.get('message')}")
        if not (
            reg_data.get("type") == FRAME_TYPE_EVENT
            and reg_data.get("event") == EVENT_REGISTER_ACK
        ):
            raise RuntimeError(
                f"manager ws expected register.ack, got type={reg_data.get('type')!r} "
                f"event={reg_data.get('event')!r}"
            )
        ack_payload = reg_data.get("payload") if isinstance(reg_data.get("payload"), dict) else {}
        ack_jiuwenclaw_id = str(ack_payload.get("jiuwenclaw_id") or "").strip()
        if not ack_jiuwenclaw_id:
            raise RuntimeError("manager ws register.ack missing jiuwenclaw_id")
        set_jiuwenclaw_id(ack_jiuwenclaw_id)
        GatewayDb.bind(ack_jiuwenclaw_id)

        self._ready = True
        logger.info(
            "[ManagerWsClient] registered jiuwenclaw_id=%s service_type=%s (register.ack ok)",
            ack_jiuwenclaw_id,
            self._service_type,
        )
        return True

    async def _recv_loop(self, ws: Any) -> None:
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(ws),
            name="manager-ws-heartbeat",
        )
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    logger.warning("[ManagerWsClient] invalid json: %s", exc)
                    continue
                await self._dispatch(ws, data)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self, ws: Any) -> None:
        """周期性发送 heartbeat，供 Manager 刷新 ``instance_info.status``。"""
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            jid = get_jiuwenclaw_id()
            if not jid:
                continue
            frame = build_heartbeat(
                jiuwenclaw_id=jid,
                service_type=self._service_type,
            )
            try:
                await ws.send(json.dumps(frame, ensure_ascii=False))
            except Exception as exc:  # noqa: BLE001
                logger.debug("[ManagerWsClient] heartbeat send failed: %s", exc)
                return

    async def _dispatch(self, ws: Any, data: dict[str, Any]) -> None:
        frame_type = data.get("type")
        if frame_type == FRAME_TYPE_CONFIG_PUSH:
            await self._handle_config_push(ws, data)
            return
        if frame_type == FRAME_TYPE_ERROR:
            payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
            logger.warning("[ManagerWsClient] server error: %s", payload.get("message"))
            return
        logger.debug("[ManagerWsClient] ignored frame type=%s", frame_type)

    async def _handle_config_push(self, ws: Any, data: dict[str, Any]) -> None:
        payload = data.get("payload")
        if not isinstance(payload, dict):
            return
        revision = str(payload.get("revision") or "")
        config = payload.get("config")
        if not isinstance(config, dict):
            config = {}

        ok = True
        err_msg: str | None = None
        sync_result: dict[str, Any] | None = None
        if self._on_config_push is not None:
            try:
                sync_result = await self._on_config_push(revision, config)
            except Exception as exc:  # noqa: BLE001
                ok = False
                err_msg = str(exc)
                logger.exception("[ManagerWsClient] on_config_push failed: %s", exc)

        ack = build_config_ack(
            revision=revision, ok=ok, error=err_msg, result=sync_result
        )
        await ws.send(json.dumps(ack, ensure_ascii=False))
        logger.info("[ManagerWsClient] config.ack revision=%s ok=%s", revision, ok)
