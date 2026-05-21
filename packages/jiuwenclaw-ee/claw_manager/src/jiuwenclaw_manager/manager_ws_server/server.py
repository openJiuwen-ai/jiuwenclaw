"""Claw Manager manager_ws_server：配置下发 WebSocket 服务端。"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any

from jiuwenclaw_manager.infrastructure.logger import get_logger
from jiuwenclaw_manager.manager_ws_server.protocol import (
    FRAME_TYPE_CONFIG_ACK,
    FRAME_TYPE_REGISTER,
    build_config_push,
    build_connection_ack,
    build_error,
    build_register_ack,
)

logger = get_logger(__name__)


@dataclass
class _ConnectedClient:
    ws: Any
    instance_id: str
    service_type: str
    service_id: str
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class _PendingConfigAck:
    event: asyncio.Event = field(default_factory=asyncio.Event)
    ok: bool = False
    error: str | None = None
    result: dict[str, Any] | None = None


class ManagerWsServer:
    """接收 Gateway manager_ws_client 长连接，用于配置下发。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8766,
        *,
        manager_id: str = "default",
        ping_interval: float | None = 30.0,
        ping_timeout: float | None = 300.0,
    ) -> None:
        self._host = host
        self._port = port
        self._manager_id = manager_id
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._server: Any = None
        self._clients: dict[int, _ConnectedClient] = {}
        self._clients_lock = asyncio.Lock()
        self._pending_acks: dict[str, _PendingConfigAck] = {}
        self._pending_acks_lock = asyncio.Lock()

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    async def start(self) -> None:
        if self._server is not None:
            logger.warning("[ManagerWsServer] already running")
            return

        try:
            from websockets.legacy.server import serve as legacy_serve

            serve_fn = legacy_serve
        except ImportError:
            import websockets

            serve_fn = websockets.serve

        self._server = await serve_fn(
            self._connection_handler,
            self._host,
            self._port,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
        )
        logger.info(
            "[ManagerWsServer] listening ws://%s:%s pid=%s",
            self._host,
            self._port,
            os.getpid(),
        )

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        async with self._clients_lock:
            self._clients.clear()
        logger.info("[ManagerWsServer] stopped")

    async def list_registered_instance_ids(self) -> list[str]:
        """返回当前已注册连接的 instance_id 列表（去重）。"""
        async with self._clients_lock:
            return sorted({c.instance_id for c in self._clients.values()})

    async def push_config_to_instance(
        self,
        instance_id: str,
        *,
        revision: str,
        config: dict[str, Any],
        service_type: str | None = "gateway",
    ) -> int:
        """向指定 instance_id 的已注册连接下发配置，返回成功发送的连接数。

        默认仅推送给 ``service_type=gateway``，避免同实例下 agent_server 等进程重复写入 Gateway 库。
        """
        frame = build_config_push(revision=revision, config=config)
        raw = json.dumps(frame, ensure_ascii=False)
        sent = 0
        st_filter = (service_type or "").strip().lower() or None
        async with self._clients_lock:
            targets = [
                c
                for c in self._clients.values()
                if c.instance_id == instance_id
                and (st_filter is None or c.service_type.lower() == st_filter)
            ]
        for client in targets:
            try:
                async with client.send_lock:
                    await client.ws.send(raw)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[ManagerWsServer] push failed instance_id=%s service_id=%s: %s",
                    instance_id,
                    client.service_id,
                    exc,
                )
        return sent

    async def push_config_to_instance_and_wait_ack(
        self,
        instance_id: str,
        *,
        revision: str,
        config: dict[str, Any],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """下发 config.push 并等待 Gateway 返回 config.ack（ok=True），返回 ack payload。"""
        pending = _PendingConfigAck()
        async with self._pending_acks_lock:
            self._pending_acks[revision] = pending
        try:
            sent = await self.push_config_to_instance(
                instance_id,
                revision=revision,
                config=config,
                service_type="gateway",
            )
            if sent < 1:
                registered = await self.list_registered_instance_ids()
                raise ValueError(
                    f"no gateway websocket connected for instance_id={instance_id!r}; "
                    f"registered_instances={registered}; "
                    "ensure gateway manager_ws_client is connected "
                    "(restart gateway after claw-manager restart)"
                )
            try:
                await asyncio.wait_for(pending.event.wait(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise ValueError(
                    f"gateway config.push ack timeout after {timeout}s "
                    f"(revision={revision!r})"
                ) from exc
            if not pending.ok:
                detail = pending.error or "gateway config sync failed"
                raise ValueError(detail)
            return {
                "revision": revision,
                "ok": True,
                "result": pending.result,
            }
        finally:
            async with self._pending_acks_lock:
                self._pending_acks.pop(revision, None)

    async def _connection_handler(self, ws: Any) -> None:
        import websockets

        remote = ws.remote_address
        key = id(ws)
        logger.info("[ManagerWsServer] new connection %s", remote)

        try:
            ack = build_connection_ack(manager_id=self._manager_id)
            await ws.send(json.dumps(ack, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ManagerWsServer] connection.ack failed: %s", exc)

        try:
            async for raw in ws:
                await self._handle_frame(ws, key, raw)
        except websockets.exceptions.ConnectionClosed:
            logger.info("[ManagerWsServer] connection closed %s", remote)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[ManagerWsServer] handler error (%s): %s", remote, exc)
        finally:
            async with self._clients_lock:
                self._clients.pop(key, None)

    async def _handle_frame(self, ws: Any, key: int, raw: str | bytes) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            err = build_error(f"invalid json: {exc}")
            await ws.send(json.dumps(err, ensure_ascii=False))
            return

        frame_type = data.get("type")
        if frame_type == FRAME_TYPE_REGISTER:
            await self._handle_register(ws, key, data)
            return
        if frame_type == FRAME_TYPE_CONFIG_ACK:
            await self._handle_config_ack(data)
            return

        err = build_error(f"unsupported frame type: {frame_type!r}")
        await ws.send(json.dumps(err, ensure_ascii=False))

    async def _handle_config_ack(self, data: dict[str, Any]) -> None:
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        revision = str(payload.get("revision") or "")
        ok = bool(payload.get("ok"))
        error = payload.get("error")
        err_text = str(error).strip() if error else None
        logger.info(
            "[ManagerWsServer] config.ack revision=%s ok=%s",
            revision,
            ok,
        )
        if not revision:
            return
        async with self._pending_acks_lock:
            pending = self._pending_acks.get(revision)
        if pending is None:
            return
        pending.ok = ok
        pending.error = err_text
        raw_result = payload.get("result")
        pending.result = raw_result if isinstance(raw_result, dict) else None
        pending.event.set()

    async def _handle_register(self, ws: Any, key: int, data: dict[str, Any]) -> None:
        payload = data.get("payload")
        if not isinstance(payload, dict):
            err = build_error("register payload must be an object")
            await ws.send(json.dumps(err, ensure_ascii=False))
            return

        instance_id = str(payload.get("instance_id") or "").strip()
        service_type = str(payload.get("service_type") or "gateway").strip()
        service_id = str(payload.get("service_id") or "").strip()
        if not instance_id:
            err = build_error("register requires instance_id")
            await ws.send(json.dumps(err, ensure_ascii=False))
            return

        client = _ConnectedClient(
            ws=ws,
            instance_id=instance_id,
            service_type=service_type,
            service_id=service_id or instance_id,
        )
        async with self._clients_lock:
            self._clients[key] = client
        try:
            ack = build_register_ack(instance_id=instance_id)
            await ws.send(json.dumps(ack, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[ManagerWsServer] register.ack failed: %s", exc)
        registered = await self.list_registered_instance_ids()
        logger.info(
            "[ManagerWsServer] registered instance_id=%s service_type=%s service_id=%s "
            "active_instances=%s pid=%s",
            instance_id,
            service_type,
            client.service_id,
            registered,
            os.getpid(),
        )


_ws_server: ManagerWsServer | None = None


def set_manager_ws_server(server: ManagerWsServer | None) -> None:
    global _ws_server
    _ws_server = server


def get_manager_ws_server() -> ManagerWsServer | None:
    return _ws_server


def _require_ws_server() -> ManagerWsServer:
    ws_server = get_manager_ws_server()
    if ws_server is None:
        raise ValueError("manager_ws_server is not running")
    return ws_server


def _revision_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def push_to_instance(
    instance_id: str,
    *,
    config: dict[str, Any],
    revision: str | None = None,
) -> dict[str, Any]:
    """向已注册 ``instance_id`` 的 Gateway 连接推送 config.push，等待 ack 并返回 ack payload。"""
    ws_server = _require_ws_server()
    rev = revision or _revision_now()
    try:
        return await ws_server.push_config_to_instance_and_wait_ack(
            instance_id,
            revision=rev,
            config=config,
        )
    except ValueError as exc:
        if "no gateway websocket connected" in str(exc):
            registered = await ws_server.list_registered_instance_ids()
            raise ValueError(
                f"no gateway websocket connected for instance_id={instance_id!r}; "
                f"registered_instances={registered}; "
                "ensure manager_ws_client is connected (restart gateway after claw-manager restart)"
            ) from exc
        raise


def _no_gateway_connected_error() -> ValueError:
    return ValueError(
        "no gateway websocket connected; registered_instances=[]; "
        "ensure each gateway manager_ws_client is connected "
        "(restart gateway after claw-manager restart)"
    )


def _adapt_payload_for_instance(
    instance_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """为指定实例复制 payload，并写入顶层 ``jiuwenclaw_id``。"""
    body = dict(payload)
    body["jiuwenclaw_id"] = instance_id
    return body


async def push_config_op(
    instance_id: str,
    config_section: str,
    payload: dict[str, Any],
    *,
    revision: str | None = None,
) -> dict[str, Any]:
    """向指定 ``instance_id`` 的 Gateway 推送配置变更（``config[config_section] = payload``）。"""
    normalized = str(instance_id or "").strip()
    if not normalized:
        raise ValueError("instance_id is required")
    section = str(config_section or "").strip()
    if not section:
        raise ValueError("config_section is required")
    body = dict(payload)
    body["jiuwenclaw_id"] = normalized
    return await push_to_instance(
        normalized,
        config={section: body},
        revision=revision,
    )


async def push_config_op_to_all(
    config_section: str,
    payload: dict[str, Any],
    *,
    adapt_payload: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """向所有已注册 Gateway 下发配置变更（逐实例独立 revision 等待 ack）。"""
    ws_server = _require_ws_server()
    instance_ids = await ws_server.list_registered_instance_ids()
    if not instance_ids:
        raise _no_gateway_connected_error()

    section = str(config_section or "").strip()
    if not section:
        raise ValueError("config_section is required")

    base_rev = _revision_now()
    last_ack: dict[str, Any] | None = None
    for iid in instance_ids:
        if adapt_payload is not None:
            body = adapt_payload(iid, payload)
        else:
            body = _adapt_payload_for_instance(iid, payload)
        last_ack = await push_config_op(
            iid,
            section,
            body,
            revision=f"{base_rev}:{iid}",
        )
    return {
        "pushed_instances": instance_ids,
        "pushed_count": len(instance_ids),
        "last_ack": last_ack,
    }
