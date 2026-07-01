# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""简单版 ``/ws`` 应用层 WebSocket 反向代理（取代原 app_web 的裸 socket 隧道）。

浏览器 WS ↔ 上游网关 WS 双向转发；**透传握手 path+query**（request_ext 等）、子协议；
按需记业务帧日志（req/res/event）。FastAPI/uvicorn 已替我们解压+解码，故无需手写帧解析。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import websockets
from fastapi import FastAPI, WebSocket
from fastapi.websockets import WebSocketDisconnect

from jiuwenclaw.webserver.common import WebRuntime

_WS_LOG_MAX = 2000


def _truncate(text: str) -> str:
    return text if len(text) <= _WS_LOG_MAX else f"{text[:_WS_LOG_MAX]}...<truncated:{len(text) - _WS_LOG_MAX}>"


def _fmt(v: Any) -> str:
    if isinstance(v, str):
        return _truncate(v)
    try:
        return _truncate(json.dumps(v, ensure_ascii=False, separators=(",", ":")))
    except TypeError:
        return _truncate(str(v))


def log_ws_business_message(logger: Any, direction: str, raw: str) -> None:
    """解析 JSON 帧并记 req/res/event 业务日志（与原 _log_ws_business_message 一致）。"""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return
    t = payload.get("type")
    if t == "req":
        logger.info("[ws][%s][req] id=%s method=%s params=%s", direction,
                    _fmt(payload.get("id")), _fmt(payload.get("method")), _fmt(payload.get("params")))
    elif t == "res":
        logger.info("[ws][%s][res] id=%s ok=%s payload=%s error=%s code=%s", direction,
                    _fmt(payload.get("id")), _fmt(payload.get("ok")), _fmt(payload.get("payload")),
                    _fmt(payload.get("error")), _fmt(payload.get("code")))
    elif t == "event":
        logger.info("[ws][%s][event] event=%s seq=%s stream_id=%s payload=%s", direction,
                    _fmt(payload.get("event")), _fmt(payload.get("seq")),
                    _fmt(payload.get("stream_id")), _fmt(payload.get("payload")))


def add_ws_proxy(app: FastAPI, rt: WebRuntime) -> None:
    """注册简单版 ``/ws`` 反代到 ``rt.ws_target``，透传 path+query+子协议。"""

    @app.websocket("/ws")
    async def _ws(ws: WebSocket) -> None:
        subprotocols = list(ws.scope.get("subprotocols") or [])
        # 上游 URL：ws_target + 原始 path + query（透传 request_ext 等）
        upstream_url = rt.ws_target.rstrip("/") + ws.url.path
        if ws.url.query:
            upstream_url += "?" + ws.url.query
        compression = None if rt.ws_disable_compress else "deflate"
        # 透传浏览器 Origin（上游 web_channel / broker 会做 Origin 校验，原裸隧道是连头一起转发的）。
        extra_headers = {}
        origin = ws.headers.get("origin")
        if origin:
            extra_headers["Origin"] = origin

        try:
            connect_kwargs: dict[str, Any] = {"compression": compression}
            if subprotocols:
                connect_kwargs["subprotocols"] = subprotocols
            if extra_headers:
                connect_kwargs["additional_headers"] = extra_headers
            upstream = await websockets.connect(upstream_url, **connect_kwargs)
        except Exception:
            rt.logger.exception("[ws] 上游连接失败: %s", upstream_url)
            await ws.close(code=1011)
            return

        negotiated = getattr(upstream, "subprotocol", None)
        await ws.accept(subprotocol=negotiated)
        rt.logger.info("[ws][handshake] tunnel established -> %s", upstream_url)

        async def browser_to_upstream() -> None:
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect()
                text = msg.get("text")
                data = msg.get("bytes")
                if text is not None:
                    log_ws_business_message(rt.logger, "frontend->backend", text)
                    await upstream.send(text)
                elif data is not None:
                    await upstream.send(data)

        async def upstream_to_browser() -> None:
            async for raw in upstream:
                if isinstance(raw, bytes):
                    await ws.send_bytes(raw)
                else:
                    log_ws_business_message(rt.logger, "backend->frontend", raw)
                    await ws.send_text(raw)

        t1 = asyncio.ensure_future(browser_to_upstream())
        t2 = asyncio.ensure_future(upstream_to_browser())
        try:
            await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (t1, t2):
                t.cancel()
            with contextlib.suppress(Exception):
                await upstream.close()
            with contextlib.suppress(Exception):
                await ws.close()
