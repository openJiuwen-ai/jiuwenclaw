# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Serve enterprise web static files and Web Pod WS (/ws + /gateway)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import threading
import uuid
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from jiuwenclaw.app_web import (
    _SpaStaticHandler,
    _default_dist_dir,
    _normalize_ws_target,
    _setup_logger,
)
from jiuwenclaw.channel.enterprise_web_uplink_config import get_enterprise_web_uplink_ws_settings
from jiuwenclaw.security.ws_origin import (
    extract_handshake_request,
    forbidden_origin_response,
    get_header_value,
    is_allowed_browser_origin,
)
from jiuwenclaw.utils import get_logs_dir, get_multi_tenant_user_workspace_dir, get_user_workspace_dir

logger = logging.getLogger(__name__)

CHAT_ACCEPT_METHODS = frozenset({
    "chat.send",
    "chat.resume",
    "chat.interrupt",
    "chat.user_answer",
})


class EnterpriseWebWsServer:
    """Web Pod WS: browsers on /ws, Gateway uplink on /gateway (req/res/event)."""

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 19000,
        browser_path: str = "/ws",
        gateway_path: str = "/gateway",
    ) -> None:
        self.host = host
        self.port = port
        self.browser_path = browser_path
        self.gateway_path = gateway_path
        self._server: Any = None
        self._running = False
        self._gateway_ws: Any | None = None
        self._gateway_lock = asyncio.Lock()
        self._connections: dict[str, Any] = {}
        self._conn_by_ws: dict[int, str] = {}
        self._session_subscribers: dict[str, set[str]] = {}
        self._pending_requests: dict[str, str] = {}
        # chat.send 等 CHAT_ACCEPT 方法立即 ack，不入 pending_requests；用此表按 request_id 路由无 session_id 的事件
        self._chat_request_routes: dict[str, str] = {}
        self._active_session: dict[str, str] = {}
        self._internal_res_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # request_ext 透传：记住每条浏览器连接握手时的 query（含透传字段），
        # 转发给 Gateway 时随帧带上，由 EnterpriseWebChannel 抽取为 ext。
        self._browser_query: dict[str, dict[str, list[str]]] = {}

    async def start(self) -> None:
        if self._running:
            return
        try:
            from websockets.legacy.server import serve as ws_serve
        except Exception:  # pragma: no cover
            import websockets

            ws_serve = websockets.serve

        uplink_ws = get_enterprise_web_uplink_ws_settings()
        self._server = await ws_serve(
            self._connection_handler,
            self.host,
            self.port,
            process_request=self._process_request,
            ping_interval=uplink_ws.ping_interval,
            ping_timeout=uplink_ws.ping_timeout,
        )
        self._running = True
        logger.info(
            "[jiuwenclaw-enterprise-web] WS 已启动: browser=ws://%s:%s%s gateway=ws://%s:%s%s",
            self.host,
            self.port,
            self.browser_path,
            self.host,
            self.port,
            self.gateway_path,
        )
        await self._server.wait_closed()

    async def stop(self) -> None:
        self._running = False
        for conn_id in list(self._connections.keys()):
            await self._teardown_browser(conn_id)
        async with self._gateway_lock:
            gw = self._gateway_ws
            self._gateway_ws = None
        if gw is not None:
            try:
                await gw.close(code=1001, reason="web pod shutdown")
            except Exception:
                logger.debug("[jiuwenclaw-enterprise-web] gateway close ignored", exc_info=True)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        logger.info("[jiuwenclaw-enterprise-web] WS 已停止")

    async def _process_request(self, *args: Any) -> Any:
        path, request_headers = extract_handshake_request(args)
        parsed = urlparse(path or "")
        request_path = parsed.path or path or ""

        if request_path == self.gateway_path:
            return None

        if request_path != self.browser_path:
            return forbidden_origin_response(args)

        origin = get_header_value(request_headers, "Origin")
        allowed = is_allowed_browser_origin(origin)
        logger.info(
            "[jiuwenclaw-enterprise-web] 握手 path=%s origin=%s allowed=%s",
            request_path,
            origin,
            allowed,
        )
        if allowed:
            return None
        return forbidden_origin_response(args)

    async def _connection_handler(self, ws: Any, path: str | None = None) -> None:
        raw_path = path if path is not None else getattr(ws, "path", "")
        parsed = urlparse(raw_path)
        request_path = parsed.path or raw_path

        if request_path == self.gateway_path:
            await self._handle_gateway(ws)
            return

        if request_path != self.browser_path:
            await ws.close(code=1008, reason=f"unsupported path: {request_path}")
            return

        await self._handle_browser(ws, parse_qs(parsed.query))

    async def _handle_gateway(self, ws: Any) -> None:
        async with self._gateway_lock:
            old = self._gateway_ws
            self._gateway_ws = ws
        if old is not None and old is not ws:
            try:
                await old.close(code=1000, reason="replaced by new gateway uplink")
            except Exception:
                logger.debug(
                    "[jiuwenclaw-enterprise-web] replaced gateway uplink close ignored",
                    exc_info=True,
                )
        logger.info("[jiuwenclaw-enterprise-web] Gateway uplink 已连接")

        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                await self.route_uplink_frame(raw)
        except Exception as exc:
            logger.warning("[jiuwenclaw-enterprise-web] Gateway uplink 异常: %s", exc)
        finally:
            async with self._gateway_lock:
                if self._gateway_ws is ws:
                    self._gateway_ws = None
            logger.info("[jiuwenclaw-enterprise-web] Gateway uplink 已断开")

    def register_browser_connection(self, conn_id: str, ws: Any) -> None:
        """Register a browser WebSocket for routing (mirrors /ws accept path)."""
        self._connections[conn_id] = ws
        self._conn_by_ws[id(ws)] = conn_id

    def bind_uplink_response_route(self, request_id: str, conn_id: str) -> None:
        """Associate a pending Gateway req id with the browser conn that sent it."""
        self._pending_requests[request_id] = conn_id

    def bind_chat_request_route(self, request_id: str, conn_id: str) -> None:
        """Associate a chat.accept request id with the browser conn that sent it."""
        self._chat_request_routes[request_id] = conn_id

    def get_chat_request_route(self, request_id: str) -> str | None:
        return self._chat_request_routes.get(request_id)

    def attach_gateway_uplink(self, ws: Any) -> None:
        """Attach the Gateway uplink WebSocket (mirrors /gateway accept path)."""
        self._gateway_ws = ws

    def subscribe_conn_to_session(self, conn_id: str, session_id: str) -> None:
        """Subscribe a browser connection to session-scoped events."""
        self._subscribe_session(conn_id, session_id)

    def get_active_session(self, conn_id: str) -> str | None:
        return self._active_session.get(conn_id)

    def session_includes_conn(self, session_id: str, conn_id: str) -> bool:
        return conn_id in self._session_subscribers.get(session_id, ())

    def has_pending_uplink_request(self, request_id: str) -> bool:
        return request_id in self._pending_requests

    async def route_browser_frame(self, conn_id: str, raw: str) -> None:
        """Route a browser req frame (mirrors /ws message handling)."""
        await self._handle_browser_frame(conn_id, raw)

    async def route_uplink_frame(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[jiuwenclaw-enterprise-web] 忽略无效 uplink JSON: %s", raw[:200])
            return
        if not isinstance(data, dict):
            return

        frame_type = data.get("type")
        if frame_type == "res":
            req_id = data.get("id")
            if not isinstance(req_id, str):
                return
            internal = self._internal_res_waiters.pop(req_id, None)
            if internal is not None and not internal.done():
                internal.set_result(data)
                return
            conn_id = self._pending_requests.pop(req_id, None)
            if conn_id is None:
                return
            browser_ws = self._connections.get(conn_id)
            if browser_ws is None:
                return
            try:
                await browser_ws.send(raw)
            except Exception as exc:
                logger.warning(
                    "[jiuwenclaw-enterprise-web] res 转发失败 conn_id=%s id=%s: %s",
                    conn_id,
                    req_id,
                    exc,
                )
            return

        if frame_type == "event":
            payload = data.get("payload")
            if not isinstance(payload, dict):
                return
            route_conn_id = payload.pop("_route_conn_id", None)
            if isinstance(route_conn_id, str):
                session_id = payload.get("session_id")
                if isinstance(session_id, str) and session_id:
                    self._active_session[route_conn_id] = session_id
                    self._session_subscribers.setdefault(session_id, set()).add(route_conn_id)
                clean = {**data, "payload": payload}
                await self._send_to_browser_conn(
                    route_conn_id,
                    json.dumps(clean, ensure_ascii=False),
                )
                return
            session_id = payload.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                request_id = data.get("request_id")
                conn_id: str | None = None
                if isinstance(request_id, str):
                    conn_id = self._pending_requests.get(request_id)
                    if conn_id is None:
                        conn_id = self._chat_request_routes.get(request_id)
                if conn_id is None:
                    return
                active_session = self._active_session.get(conn_id)
                if isinstance(active_session, str) and active_session:
                    enriched = {
                        **data,
                        "payload": {**payload, "session_id": active_session},
                    }
                    await self._send_to_browser_conn(
                        conn_id,
                        json.dumps(enriched, ensure_ascii=False),
                    )
                else:
                    logger.warning(
                        "[jiuwenclaw-enterprise-web] 丢弃无 session_id 且无法注入 active_session 的事件 "
                        "conn_id=%s request_id=%s event=%s",
                        conn_id,
                        request_id if isinstance(request_id, str) else "",
                        data.get("event"),
                    )
                return
            for conn_id in list(self._session_subscribers.get(session_id, ())):
                await self._send_to_browser_conn(conn_id, raw)

    async def _send_to_browser_conn(self, conn_id: str, raw: str) -> None:
        browser_ws = self._connections.get(conn_id)
        if browser_ws is None:
            return
        try:
            await browser_ws.send(raw)
        except Exception as exc:
            logger.warning(
                "[jiuwenclaw-enterprise-web] event 转发失败 conn_id=%s: %s",
                conn_id,
                exc,
            )

    async def _uplink_connected(self) -> bool:
        async with self._gateway_lock:
            return self._gateway_ws is not None

    def _inject_browser_query(self, conn_id: str, data: dict[str, Any], raw: str) -> str:
        """把该浏览器连接握手 query 附到转发帧上，供 Gateway 抽取为 request_ext。

        无 query 时原样返回 raw（零行为变更）。
        """
        bq = self._browser_query.get(conn_id)
        if not bq:
            return raw
        return json.dumps({**data, "_browser_query": bq}, ensure_ascii=False)

    async def _send_to_gateway(self, payload: str) -> bool:
        async with self._gateway_lock:
            gw = self._gateway_ws
        if gw is None:
            return False
        try:
            await gw.send(payload)
            return True
        except Exception as exc:
            logger.warning("[jiuwenclaw-enterprise-web] 向 Gateway 发送失败: %s", exc)
            return False

    async def _handle_browser(self, ws: Any, query: dict[str, list[str]] | None = None) -> None:
        conn_id = str(uuid.uuid4())
        remote = getattr(ws, "remote_address", None)
        self._connections[conn_id] = ws
        self._conn_by_ws[id(ws)] = conn_id
        # 记住握手 query（含 request_ext 透传字段），随每帧转发给 Gateway。
        self._browser_query[conn_id] = query or {}
        logger.info("[jiuwenclaw-enterprise-web] 浏览器连接: conn_id=%s remote=%s", conn_id, remote)

        await self.request_gateway_connection_ack(conn_id)

        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                await self._handle_browser_frame(conn_id, raw)
        except Exception as exc:
            logger.warning("[jiuwenclaw-enterprise-web] 浏览器连接异常 conn_id=%s: %s", conn_id, exc)
        finally:
            await self._teardown_browser(conn_id)
            logger.info("[jiuwenclaw-enterprise-web] 浏览器断开: conn_id=%s", conn_id)

    async def _teardown_browser(self, conn_id: str) -> None:
        ws = self._connections.pop(conn_id, None)
        if ws is not None:
            self._conn_by_ws.pop(id(ws), None)
        self._browser_query.pop(conn_id, None)
        session_id = self._active_session.pop(conn_id, None)
        if session_id:
            subs = self._session_subscribers.get(session_id)
            if subs:
                subs.discard(conn_id)
                if not subs:
                    self._session_subscribers.pop(session_id, None)
        stale_reqs = [rid for rid, cid in self._pending_requests.items() if cid == conn_id]
        for rid in stale_reqs:
            self._pending_requests.pop(rid, None)
        stale_chat = [rid for rid, cid in self._chat_request_routes.items() if cid == conn_id]
        for rid in stale_chat:
            self._chat_request_routes.pop(rid, None)

    async def request_gateway_connection_ack(self, conn_id: str) -> None:
        """通知 Gateway 为浏览器连接生成 connection.ack（逻辑归属 Gateway）."""
        if not await self._uplink_connected():
            logger.debug(
                "[jiuwenclaw-enterprise-web] uplink 不可用，跳过 connection.ack 请求 conn_id=%s",
                conn_id,
            )
            return
        req_id = f"web-conn-ack-{uuid.uuid4().hex}"
        req = {
            "type": "req",
            "id": req_id,
            "method": "web.connection_ack",
            "params": {"conn_id": conn_id},
        }
        if not await self._send_to_gateway(json.dumps(req, ensure_ascii=False)):
            logger.debug(
                "[jiuwenclaw-enterprise-web] connection.ack 请求发送失败 conn_id=%s",
                conn_id,
            )

    def _subscribe_session(self, conn_id: str, session_id: str | None) -> None:
        if not isinstance(session_id, str) or not session_id:
            session_id = self._active_session.get(conn_id)
        if not isinstance(session_id, str) or not session_id:
            return
        self._active_session[conn_id] = session_id
        self._session_subscribers.setdefault(session_id, set()).add(conn_id)

    async def _handle_browser_frame(self, conn_id: str, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await self._respond_browser(
                conn_id,
                {"type": "res", "id": "", "ok": False, "error": "invalid json", "code": "BAD_REQUEST"},
            )
            return
        if not isinstance(data, dict) or data.get("type") != "req":
            return

        req_id = data.get("id")
        method = data.get("method")
        params = data.get("params")
        if not isinstance(req_id, str) or not isinstance(method, str):
            await self._respond_browser(
                conn_id,
                {
                    "type": "res",
                    "id": req_id if isinstance(req_id, str) else "",
                    "ok": False,
                    "error": "invalid request",
                    "code": "BAD_REQUEST",
                },
            )
            return
        if not isinstance(params, dict):
            params = {}

        session_id = params.get("session_id")
        if isinstance(session_id, str) and session_id:
            self._subscribe_session(conn_id, session_id)

        if method in CHAT_ACCEPT_METHODS:
            self._chat_request_routes[req_id] = conn_id
            ack_session = (
                session_id
                if isinstance(session_id, str) and session_id
                else self._active_session.get(conn_id, "")
            )
            await self._respond_browser(
                conn_id,
                {
                    "type": "res",
                    "id": req_id,
                    "ok": True,
                    "payload": {"accepted": True, "session_id": ack_session},
                },
            )
            if not await self._uplink_connected():
                return
            if not await self._send_to_gateway(self._inject_browser_query(conn_id, data, raw)):
                return
            return

        if not await self._uplink_connected():
            await self._respond_browser(
                conn_id,
                {
                    "type": "res",
                    "id": req_id,
                    "ok": False,
                    "error": "gateway uplink not connected",
                    "code": "UPLINK_UNAVAILABLE",
                },
            )
            return

        self._pending_requests[req_id] = conn_id
        if not await self._send_to_gateway(self._inject_browser_query(conn_id, data, raw)):
            self._pending_requests.pop(req_id, None)
            await self._respond_browser(
                conn_id,
                {
                    "type": "res",
                    "id": req_id,
                    "ok": False,
                    "error": "gateway uplink send failed",
                    "code": "UPLINK_UNAVAILABLE",
                },
            )

    async def _respond_browser(self, conn_id: str, frame: dict[str, Any]) -> None:
        ws = self._connections.get(conn_id)
        if ws is None:
            return
        try:
            await ws.send(json.dumps(frame, ensure_ascii=False))
        except Exception as exc:
            logger.warning("[jiuwenclaw-enterprise-web] 浏览器回包失败 conn_id=%s: %s", conn_id, exc)


def _run_http_server(
    *,
    host: str,
    port: int,
    dist_dir: Path,
    api_target: str,
    ws_target: str,
    ws_disable_compress: bool,
    project_root: Path,
    workspace_root: Path,
    logs_root: Path,
    log_level: str,
) -> None:
    file_logger = _setup_logger(logs_root, log_level)

    class _ConfiguredHandler(_SpaStaticHandler):
        pass

    _ConfiguredHandler.api_target = api_target
    _ConfiguredHandler.ws_target = ws_target
    _ConfiguredHandler.ws_disable_compress = ws_disable_compress
    _ConfiguredHandler.project_root = project_root
    _ConfiguredHandler.workspace_root = workspace_root
    _ConfiguredHandler.logs_root = logs_root
    _ConfiguredHandler.logger = file_logger

    handler = partial(_ConfiguredHandler, directory=str(dist_dir))
    server = ThreadingHTTPServer((host, port), handler)
    file_logger.info("[jiuwenclaw-enterprise-web] serving %s", dist_dir)
    file_logger.info("[jiuwenclaw-enterprise-web] http://%s:%s", host, port)
    file_logger.info("[jiuwenclaw-enterprise-web] /ws proxy -> %s", ws_target)
    file_logger.info(
        "[jiuwenclaw-enterprise-web] WS ws://%s:%s/ws (browser) /gateway (uplink)",
        os.getenv("ENTERPRISE_WEB_WS_HOST", "0.0.0.0"),
        os.getenv("ENTERPRISE_WEB_WS_PORT", "19000"),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        file_logger.info("[jiuwenclaw-enterprise-web] http server closed")


async def _run_ws_server(
    *,
    host: str,
    port: int,
    browser_path: str,
    gateway_path: str,
) -> None:
    ws_server = EnterpriseWebWsServer(
        host=host,
        port=port,
        browser_path=browser_path,
        gateway_path=gateway_path,
    )
    try:
        await ws_server.start()
    except asyncio.CancelledError:
        await ws_server.stop()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve enterprise web static files and Web Pod WebSocket server.",
    )
    parser.add_argument("--host", default=os.getenv("JIUWENCLAW_WEB_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("JIUWENCLAW_WEB_PORT", "5173")),
        help="HTTP port for static files.",
    )
    parser.add_argument("--dist", default=str(_default_dist_dir()))
    parser.add_argument(
        "--ws-target",
        default="",
        help="Override /ws tunnel target on HTTP port (default: local WS server).",
    )
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--ws-disable-compress", action="store_true")
    parser.add_argument(
        "--relay-host",
        default=os.getenv("ENTERPRISE_WEB_WS_HOST", "0.0.0.0"),
        help="WebSocket bind host (browser /ws and gateway /gateway).",
    )
    parser.add_argument(
        "--relay-port",
        type=int,
        default=int(os.getenv("ENTERPRISE_WEB_WS_PORT", os.getenv("WEB_PORT", "19000"))),
        help="WebSocket bind port (browser /ws and gateway /gateway).",
    )
    parser.add_argument(
        "--relay-browser-path",
        default=os.getenv("ENTERPRISE_WEB_BROWSER_PATH", os.getenv("WEB_PATH", "/ws")),
    )
    parser.add_argument(
        "--relay-gateway-path",
        default=os.getenv("ENTERPRISE_WEB_GATEWAY_PATH", "/gateway"),
    )
    parser.add_argument(
        "--relay-only",
        action="store_true",
        help="Only run WebSocket server (no HTTP static server).",
    )
    args = parser.parse_args()

    dist_dir = Path(args.dist).expanduser().resolve()
    if not args.relay_only:
        if not dist_dir.exists() or not dist_dir.is_dir():
            raise SystemExit(f"dist directory not found or invalid: {dist_dir}")

    relay_port = args.relay_port
    default_ws = f"ws://127.0.0.1:{relay_port}{args.relay_browser_path.rstrip('/') or '/ws'}"
    if not default_ws.endswith("/ws") and args.relay_browser_path == "/ws":
        default_ws = f"ws://127.0.0.1:{relay_port}/ws"

    try:
        ws_target = _normalize_ws_target(args.ws_target.strip() or default_ws)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    project_root = get_user_workspace_dir()
    workspace_root = get_multi_tenant_user_workspace_dir("default", "default") or project_root
    logs_root = get_logs_dir().resolve()

    if not args.relay_only:
        http_thread = threading.Thread(
            target=_run_http_server,
            kwargs={
                "host": args.host,
                "port": args.port,
                "dist_dir": dist_dir,
                "api_target": "",
                "ws_target": ws_target,
                "ws_disable_compress": args.ws_disable_compress,
                "project_root": project_root,
                "workspace_root": workspace_root,
                "logs_root": logs_root,
                "log_level": args.log_level,
            },
            name="enterprise-web-http",
            daemon=True,
        )
        http_thread.start()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ws_task = loop.create_task(
        _run_ws_server(
            host=args.relay_host,
            port=relay_port,
            browser_path=args.relay_browser_path,
            gateway_path=args.relay_gateway_path,
        ),
        name="enterprise-web-ws",
    )

    def _shutdown(*_args: object) -> None:
        ws_task.cancel()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    logger.info(
        "[jiuwenclaw-enterprise-web] HTTP http://%s:%s | WS ws://%s:%s%s",
        args.host,
        args.port,
        args.relay_host,
        relay_port,
        args.relay_browser_path,
    )
    try:
        loop.run_until_complete(ws_task)
    except asyncio.CancelledError:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
