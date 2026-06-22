# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""控制链路握手鉴权（link-auth）系统测试 —— 在**真实 WebSocket 连接**上跑一次握手
（非对称 Ed25519 + TOFU 指纹固定）。

为保持自包含、不依赖生产 server 接线，测试内自行用 link_auth 搭一个最小 ws 服务端：
``process_request`` 用 ``verify_and_pin(server_pin_store, token, expect_type="gateway", ...)``
做与生产握手一致的校验，不通过即回 401；连接建立后在 connection.ack 里放服务端自己签的
manager 令牌，由客户端反向 ``verify_and_pin`` 核验。覆盖：

- enforce + 合法令牌 → 升级成功，且客户端能反向核验 connection.ack 里 Manager 的令牌；
- enforce + 无令牌    → 协议升级前被拒（HTTP 401）；
- enforce + 冒充      → 同 iss 换密钥对，服务端 TOFU 指纹固定拒（HTTP 401）；
- off                 → 不鉴权，无令牌也放行（行为与引入前一致）。

仅依赖 link_auth + websockets，不拉起 DB/fastapi，可在 CI 直接跑；ST 自包含，不依赖
生产 server。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
from contextlib import contextmanager

import pytest
import websockets

pytestmark = [pytest.mark.integration, pytest.mark.system]


# link-auth 已收敛进 runtime（foundation 层）。用 import_module 而非 import 语句：
# 模块级动态导入，既不触发 E402，也不引入函数内嵌套 import。
cla = importlib.import_module("openjiuwen_runtime.foundation.security.link_auth")

# 服务端（manager）与客户端（gateway）各持一对持久身份密钥对。
SERVER_PRIV, SERVER_PUB = cla.generate_keypair()
CLIENT_PRIV, CLIENT_PUB = cla.generate_keypair()

_UNAUTHORIZED_BODY = b"Unauthorized: link token invalid\n"


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@contextmanager
def _link_env(*, mode: str):
    """临时设置 link-auth 环境，退出时还原（服务端与客户端同进程共享环境）。"""
    saved = os.environ.get("CLAW_LINK_AUTH_MODE")
    os.environ["CLAW_LINK_AUTH_MODE"] = mode
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("CLAW_LINK_AUTH_MODE", None)
        else:
            os.environ["CLAW_LINK_AUTH_MODE"] = saved


def _extract_headers(args: tuple):
    """从 legacy ``(path, headers)`` 或 new ``(connection, request)`` 取出 headers 容器。"""
    if len(args) >= 2:
        first, second = args[0], args[1]
        if isinstance(first, str):
            return second
        return getattr(second, "headers", second)
    if len(args) == 1:
        return getattr(args[0], "headers", None)
    return None


def _get_header(headers, name: str) -> str | None:
    if headers is None:
        return None
    get = getattr(headers, "get", None)
    if callable(get):
        value = get(name)
        if value is None:
            value = get(name.lower())
        return str(value) if value is not None else None
    return None


def _unauthorized_response(args: tuple):
    headers = [
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", str(len(_UNAUTHORIZED_BODY))),
    ]
    if args and not isinstance(args[0], str):
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        return Response(401, "Unauthorized", Headers(headers), _UNAUTHORIZED_BODY)
    return 401, headers, _UNAUTHORIZED_BODY


async def _start_server(host: str, port: int):
    """起一个用 verify_and_pin 做 process_request 的最小 WS 服务端（自包含，不依赖生产 server）。"""
    cache = cla.NonceCache()
    pin_store = cla.InMemoryPinStore()

    def process_request(*args):
        headers = _extract_headers(args)
        token = _get_header(headers, cla.LINK_TOKEN_HEADER)
        result = cla.verify_and_pin(
            pin_store, token, expect_type="gateway", nonce_cache=cache
        )
        if result.allowed:
            return None
        return _unauthorized_response(args)

    async def handler(ws):
        # 模拟 server.py：在 connection.ack 里附 Manager 自己签的反向令牌。
        ack = {"type": "event", "event": "connection.ack", "payload": {"manager_id": "mgr-1"}}
        tok = cla.build_token(
            service_id="mgr-1",
            service_type="manager",
            private_b64=SERVER_PRIV,
            public_b64=SERVER_PUB,
        )
        if tok:
            ack["payload"]["link_token"] = tok
        await ws.send(json.dumps(ack))
        try:
            await asyncio.wait_for(ws.recv(), timeout=1.0)
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            pass

    return await websockets.serve(handler, host, port, process_request=process_request)


async def _connect(uri: str, headers: dict | None):
    """跨 websockets 版本的客户端连接（additional_headers / extra_headers）。"""
    try:
        return await websockets.connect(uri, additional_headers=headers or None)
    except TypeError:
        return await websockets.connect(uri, extra_headers=headers or None)


def _reject_status(exc: Exception):
    resp = getattr(exc, "response", None)
    if resp is not None and hasattr(resp, "status_code"):
        return resp.status_code
    return getattr(exc, "status_code", None)


# --------------------------------------------------------------------------
# 正向：enforce 合法令牌 → 连接成功 + 反向核验通过
# --------------------------------------------------------------------------

def test_enforce_valid_token_connects_and_reverse_verified():
    async def scenario():
        port = _free_port()
        server = await _start_server("127.0.0.1", port)
        client_pin_store = cla.InMemoryPinStore()
        try:
            headers = cla.build_token_header(
                service_id="gw-1",
                service_type="gateway",
                private_b64=CLIENT_PRIV,
                public_b64=CLIENT_PUB,
            )
            assert cla.LINK_TOKEN_HEADER in headers  # enforce 下必有令牌
            ws = await _connect(f"ws://127.0.0.1:{port}", headers)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                data = json.loads(raw)
                assert data["event"] == "connection.ack"
                # 客户端反向核验 Manager 的令牌（含 TOFU 指纹固定）。
                res = cla.verify_and_pin(
                    client_pin_store,
                    data["payload"].get("link_token"),
                    expect_type="manager",
                )
                assert res.allowed is True and res.ok is True
            finally:
                await ws.close()
        finally:
            server.close()
            await server.wait_closed()

    with _link_env(mode="enforce"):
        asyncio.run(scenario())


# --------------------------------------------------------------------------
# 反向：enforce 无令牌 → 升级前被拒（401）
# --------------------------------------------------------------------------

def test_enforce_missing_token_rejected_401():
    async def scenario():
        port = _free_port()
        server = await _start_server("127.0.0.1", port)
        try:
            with pytest.raises(websockets.exceptions.InvalidHandshake) as ei:
                await _connect(f"ws://127.0.0.1:{port}", None)  # 不带令牌
            assert _reject_status(ei.value) == 401
        finally:
            server.close()
            await server.wait_closed()

    with _link_env(mode="enforce"):
        asyncio.run(scenario())


# --------------------------------------------------------------------------
# 冒充：enforce 同 iss 换密钥对 → 服务端 TOFU 指纹固定拒（401）
# --------------------------------------------------------------------------

def test_enforce_impersonation_rejected_401():
    async def scenario():
        port = _free_port()
        server = await _start_server("127.0.0.1", port)
        try:
            # 第一次：合法 gateway 密钥对，连接成功（服务端记录其指纹）。
            good = cla.build_token_header(
                service_id="gw-1",
                service_type="gateway",
                private_b64=CLIENT_PRIV,
                public_b64=CLIENT_PUB,
            )
            ws = await _connect(f"ws://127.0.0.1:{port}", good)
            await ws.close()

            # 第二次：同 iss=gw-1，但换一对全新密钥对冒充 → 指纹不匹配，401。
            evil_priv, evil_pub = cla.generate_keypair()
            evil = cla.build_token_header(
                service_id="gw-1",
                service_type="gateway",
                private_b64=evil_priv,
                public_b64=evil_pub,
            )
            with pytest.raises(websockets.exceptions.InvalidHandshake) as ei:
                await _connect(f"ws://127.0.0.1:{port}", evil)
            assert _reject_status(ei.value) == 401
        finally:
            server.close()
            await server.wait_closed()

    with _link_env(mode="enforce"):
        asyncio.run(scenario())


# --------------------------------------------------------------------------
# 开关 off：无令牌也放行（行为与引入前一致）
# --------------------------------------------------------------------------

def test_off_mode_connects_without_token():
    async def scenario():
        port = _free_port()
        server = await _start_server("127.0.0.1", port)
        try:
            ws = await _connect(f"ws://127.0.0.1:{port}", None)
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                assert json.loads(raw)["event"] == "connection.ack"
            finally:
                await ws.close()
        finally:
            server.close()
            await server.wait_closed()

    with _link_env(mode="off"):
        asyncio.run(scenario())
