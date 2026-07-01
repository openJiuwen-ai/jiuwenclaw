# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""企业版 Web Pod 有状态 WS broker（FastAPI 版）。

``EnterpriseWebWsServer`` 保留与原实现一致的状态机与公开 API（9 个方法 + CHAT_ACCEPT_METHODS），
对端 ws 对象只需提供 ``async send(data)``（与单测的 FakeBrowser/FakeGateway 一致）。
原先基于 ``websockets.serve`` 的"绑端口 / 收帧循环"改由 FastAPI ``@app.websocket`` 端点驱动。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

from fastapi import FastAPI, WebSocket
from fastapi.websockets import WebSocketDisconnect

from jiuwenclaw.security.ws_origin import is_allowed_browser_origin

CHAT_ACCEPT_METHODS = frozenset({
    "chat.send",
    "chat.resume",
    "chat.interrupt",
    "chat.user_answer",
})


class EnterpriseWebWsServer:
    """浏览器(/ws) ↔ 单个网关 uplink(/gateway) 的有状态多路复用 broker。

    路由/状态逻辑与原 app_enterprise_web.EnterpriseWebWsServer 完全一致；对端 ws 对象只需有
    ``async def send(data: str)``。绑端口/收帧循环由 FastAPI 端点（见 add_enterprise_ws_routes）驱动。
    """

    def __init__(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 19000,
        browser_path: str = "/ws",
        gateway_path: str = "/gateway",
        logger: Any = None,
    ) -> None:
        self.host = host
        self.port = port
        self.browser_path = browser_path
        self.gateway_path = gateway_path
        self._gateway_ws: Any | None = None
        self._gateway_lock = asyncio.Lock()
        self._connections: dict[str, Any] = {}
        self._conn_by_ws: dict[int, str] = {}
        self._session_subscribers: dict[str, set[str]] = {}
        self._pending_requests: dict[str, str] = {}
        # chat 请求 id → 浏览器连接（持久绑定，供 chat 事件流在无 session_id 时回程）
        self._chat_request_routes: dict[str, str] = {}
        self._active_session: dict[str, str] = {}
        self._internal_res_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # request_ext 透传：记住每条浏览器连接握手时的 query（含透传字段）。
        self._browser_query: dict[str, dict[str, list[str]]] = {}
        self._logger = logger or logging.getLogger("jiuwenclaw.webserver")

    @property
    def logger(self) -> Any:
        """broker 日志器（供端点等外部协作者使用，避免直接访问受保护成员）。"""
        return self._logger

    # ---- 公开 API（单测契约，行为与原实现一致）----
    def register_browser_connection(self, conn_id: str, ws: Any) -> None:
        self._connections[conn_id] = ws
        self._conn_by_ws[id(ws)] = conn_id

    def record_browser_query(self, conn_id: str, query: dict[str, list[str]]) -> None:
        """记录浏览器连接握手 query（request_ext 透传，供端点写入）。"""
        self._browser_query[conn_id] = query

    def bind_uplink_response_route(self, request_id: str, conn_id: str) -> None:
        self._pending_requests[request_id] = conn_id

    def bind_chat_request_route(self, request_id: str, conn_id: str) -> None:
        """关联 chat 请求 id 与发起它的浏览器连接（chat 事件流回程，持久绑定）。"""
        self._chat_request_routes[request_id] = conn_id

    def get_chat_request_route(self, request_id: str) -> str | None:
        return self._chat_request_routes.get(request_id)

    def attach_gateway_uplink(self, ws: Any) -> None:
        self._gateway_ws = ws

    async def attach_gateway_exclusive(self, sender: Any) -> Any:
        """接入新的 gateway uplink，返回被它替换掉的旧连接（如有）。"""
        async with self._gateway_lock:
            old = self._gateway_ws
            self._gateway_ws = sender
        return old

    async def detach_gateway(self, sender: Any) -> None:
        """断开指定 gateway uplink（仅当当前仍是它时才清空）。"""
        async with self._gateway_lock:
            if self._gateway_ws is sender:
                self._gateway_ws = None

    def subscribe_conn_to_session(self, conn_id: str, session_id: str) -> None:
        self._subscribe_session(conn_id, session_id)

    def get_active_session(self, conn_id: str) -> str | None:
        return self._active_session.get(conn_id)

    def session_includes_conn(self, session_id: str, conn_id: str) -> bool:
        return conn_id in self._session_subscribers.get(session_id, ())

    def has_pending_uplink_request(self, request_id: str) -> bool:
        return request_id in self._pending_requests

    # ---- 网关 → 浏览器路由 ----
    async def route_uplink_frame(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._logger.warning("[jiuwenclaw-enterprise-web] 忽略无效 uplink JSON: %s", raw[:200])
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
            except Exception:
                self._logger.exception("[jiuwenclaw-enterprise-web] res 转发失败 conn_id=%s id=%s", conn_id, req_id)
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
                await self._send_to_browser_conn(route_conn_id, json.dumps(clean, ensure_ascii=False))
                return
            session_id = payload.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                request_id = data.get("request_id")
                conn_id = None
                if isinstance(request_id, str):
                    conn_id = self._pending_requests.get(request_id)
                    if conn_id is None:
                        conn_id = self._chat_request_routes.get(request_id)
                if conn_id is None:
                    return
                # 无 session_id 的事件按 request_id 回程，并补上该连接的 active_session 再下发
                active_session = self._active_session.get(conn_id)
                if isinstance(active_session, str) and active_session:
                    enriched = {**data, "payload": {**payload, "session_id": active_session}}
                    await self._send_to_browser_conn(conn_id, json.dumps(enriched, ensure_ascii=False))
                else:
                    self._logger.warning(
                        "[jiuwenclaw-enterprise-web] 丢弃无 session_id 且无法注入 active_session 的事件 "
                        "conn_id=%s request_id=%s event=%s",
                        conn_id, request_id if isinstance(request_id, str) else "", data.get("event"),
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
        except Exception:
            self._logger.exception("[jiuwenclaw-enterprise-web] event 转发失败 conn_id=%s", conn_id)

    async def _uplink_connected(self) -> bool:
        async with self._gateway_lock:
            return self._gateway_ws is not None

    def _inject_browser_query(self, conn_id: str, data: dict[str, Any], raw: str) -> str:
        """把该浏览器连接握手 query 附到转发帧上（无 query 原样返回，零变更）。"""
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
        except Exception:
            self._logger.exception("[jiuwenclaw-enterprise-web] 向 Gateway 发送失败")
            return False

    async def request_gateway_connection_ack(self, conn_id: str) -> None:
        """通知 Gateway 为浏览器连接生成 connection.ack（逻辑归属 Gateway）。"""
        if not await self._uplink_connected():
            self._logger.debug("[jiuwenclaw-enterprise-web] uplink 不可用，跳过 connection.ack conn_id=%s", conn_id)
            return
        req_id = f"web-conn-ack-{uuid.uuid4().hex}"
        req = {"type": "req", "id": req_id, "method": "web.connection_ack", "params": {"conn_id": conn_id}}
        if not await self._send_to_gateway(json.dumps(req, ensure_ascii=False)):
            self._logger.debug("[jiuwenclaw-enterprise-web] connection.ack 请求发送失败 conn_id=%s", conn_id)

    def _subscribe_session(self, conn_id: str, session_id: str | None) -> None:
        if not isinstance(session_id, str) or not session_id:
            session_id = self._active_session.get(conn_id)
        if not isinstance(session_id, str) or not session_id:
            return
        self._active_session[conn_id] = session_id
        self._session_subscribers.setdefault(session_id, set()).add(conn_id)

    async def _respond_browser(self, conn_id: str, frame: dict[str, Any]) -> None:
        ws = self._connections.get(conn_id)
        if ws is None:
            return
        try:
            await ws.send(json.dumps(frame, ensure_ascii=False))
        except Exception:
            self._logger.exception("[jiuwenclaw-enterprise-web] 浏览器回包失败 conn_id=%s", conn_id)

    async def teardown_browser(self, conn_id: str) -> None:
        """浏览器断开后的清理（公开入口）。"""
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
        for rid in [r for r, c in self._pending_requests.items() if c == conn_id]:
            self._pending_requests.pop(rid, None)
        for rid in [r for r, c in self._chat_request_routes.items() if c == conn_id]:
            self._chat_request_routes.pop(rid, None)

    # ---- 浏览器 → 网关 ----
    async def route_browser_frame(self, conn_id: str, raw: str) -> None:
        """处理一帧浏览器上行（公开入口）。"""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await self._respond_browser(conn_id, {
                "type": "res", "id": "", "ok": False,
                "error": "invalid json", "code": "BAD_REQUEST",
            })
            return
        if not isinstance(data, dict) or data.get("type") != "req":
            return
        req_id = data.get("id")
        method = data.get("method")
        params = data.get("params")
        if not isinstance(req_id, str) or not isinstance(method, str):
            await self._respond_browser(conn_id, {
                "type": "res", "id": req_id if isinstance(req_id, str) else "",
                "ok": False, "error": "invalid request", "code": "BAD_REQUEST",
            })
            return
        if not isinstance(params, dict):
            params = {}
        session_id = params.get("session_id")
        if isinstance(session_id, str) and session_id:
            self._subscribe_session(conn_id, session_id)
        if method in CHAT_ACCEPT_METHODS:
            # 持久绑定 chat 请求 id → 本连接，供后续 event 流（含无 session_id 的早期事件）回程
            self._chat_request_routes[req_id] = conn_id
            ack_session = (
                session_id if isinstance(session_id, str) and session_id
                else self._active_session.get(conn_id, "")
            )
            await self._respond_browser(conn_id, {
                "type": "res", "id": req_id, "ok": True,
                "payload": {"accepted": True, "session_id": ack_session},
            })
            if not await self._uplink_connected():
                return
            await self._send_to_gateway(self._inject_browser_query(conn_id, data, raw))
            return
        if not await self._uplink_connected():
            await self._respond_browser(conn_id, {
                "type": "res", "id": req_id, "ok": False,
                "error": "gateway uplink not connected", "code": "UPLINK_UNAVAILABLE",
            })
            return
        self._pending_requests[req_id] = conn_id
        if not await self._send_to_gateway(self._inject_browser_query(conn_id, data, raw)):
            self._pending_requests.pop(req_id, None)
            await self._respond_browser(conn_id, {
                "type": "res", "id": req_id, "ok": False,
                "error": "gateway uplink send failed", "code": "UPLINK_UNAVAILABLE",
            })


class _FastapiWsSender:
    """把 FastAPI WebSocket 适配成 broker 期望的 ``async send(data)`` / ``close()`` 接口。"""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def send(self, data: str) -> None:
        await self._ws.send_text(data)

    async def close(self, code: int = 1000) -> None:
        with contextlib.suppress(Exception):
            await self._ws.close(code=code)


def add_enterprise_ws_routes(app: FastAPI, broker: EnterpriseWebWsServer) -> None:
    """在 app 上注册浏览器 ``/ws`` 与网关 uplink ``/gateway`` 两个 WebSocket 端点。"""

    async def _browser_endpoint(ws: WebSocket) -> None:
        origin = ws.headers.get("origin")
        if not is_allowed_browser_origin(origin):
            broker.logger.info("[jiuwenclaw-enterprise-web] 握手拒绝 origin=%s", origin)
            await ws.close(code=1008)
            return
        await ws.accept()
        conn_id = str(uuid.uuid4())
        sender = _FastapiWsSender(ws)
        broker.register_browser_connection(conn_id, sender)
        broker.record_browser_query(conn_id, _multi_qs(ws))
        broker.logger.info("[jiuwenclaw-enterprise-web] 浏览器连接: conn_id=%s", conn_id)
        await broker.request_gateway_connection_ack(conn_id)
        try:
            while True:
                raw = await ws.receive_text()
                await broker.route_browser_frame(conn_id, raw)
        except WebSocketDisconnect:
            pass
        except Exception:
            broker.logger.exception("[jiuwenclaw-enterprise-web] 浏览器连接异常 conn_id=%s", conn_id)
        finally:
            await broker.teardown_browser(conn_id)
            broker.logger.info("[jiuwenclaw-enterprise-web] 浏览器断开: conn_id=%s", conn_id)

    async def _gateway_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        sender = _FastapiWsSender(ws)
        old = await broker.attach_gateway_exclusive(sender)
        if old is not None and old is not sender:
            await old.close(code=1000)
        broker.logger.info("[jiuwenclaw-enterprise-web] Gateway uplink 已连接")
        try:
            while True:
                raw = await ws.receive_text()
                await broker.route_uplink_frame(raw)
        except WebSocketDisconnect:
            pass
        except Exception:
            broker.logger.exception("[jiuwenclaw-enterprise-web] Gateway uplink 异常")
        finally:
            await broker.detach_gateway(sender)
            broker.logger.info("[jiuwenclaw-enterprise-web] Gateway uplink 已断开")

    app.add_api_websocket_route(broker.browser_path, _browser_endpoint)
    app.add_api_websocket_route(broker.gateway_path, _gateway_endpoint)


def _multi_qs(ws: WebSocket) -> dict[str, list[str]]:
    """把 FastAPI query_params 还原成 parse_qs 形态 dict[str, list[str]]（透传契约一致）。"""
    out: dict[str, list[str]] = {}
    for k, v in ws.query_params.multi_items():
        out.setdefault(k, []).append(v)
    return out
