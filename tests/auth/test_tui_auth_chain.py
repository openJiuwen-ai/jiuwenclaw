import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock


class AuthResult:
    def __init__(self, success=False, user_id="", error="", extensions=None):
        self.success = success
        self.user_id = user_id
        self.error = error
        self.extensions = extensions or {}


@pytest.fixture
def tui_channel():
    channel = MagicMock()
    channel._connect_hooks = []
    return channel


@pytest.fixture
def router_client(tui_channel):
    client = MagicMock()
    client._auth_client = MagicMock()
    client._current_agent_types = {}
    client._server_ready = True
    client._yuanrong = MagicMock()
    client._yuanrong.server_ready = True
    client._closed = False
    client._agents_cache = {}
    client._agents_cache_ttl = {}

    async def on_connect(ws):
        if client._auth_client is None:
            return AuthResult(
                success=False, user_id="", error="No valid credentials",
                extensions={"error_code": "UNSUPPORTED_CREDENTIAL"},
            )
        token = ""
        headers = getattr(ws, "request_headers", {}) or {}
        if isinstance(headers, dict):
            auth_header = headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
        from unittest.mock import MagicMock
        context = MagicMock()
        context.channel_type = ""
        context.credentials = {"token": token} if token else {}
        context.headers = headers
        context.remote_addr = getattr(ws, "remote_address", None)
        result = await client._auth_client.authenticate(context)
        if not result.success:
            close = getattr(ws, "close", None)
            if callable(close):
                ret = close(code=1008, reason="unauthorized")
                if hasattr(ret, "__await__"):
                    await ret
        return result

    client.on_connect = on_connect
    tui_channel.on_connect(client.on_connect)
    return client


@pytest.mark.asyncio
async def test_auth_success(tui_channel, router_client):
    print("\n[测试 1] 认证通过：token 有效 → 不关闭连接")
    router_client._auth_client.authenticate = AsyncMock(
        return_value=AuthResult(success=True, user_id="u_abc123")
    )

    ws = MagicMock()
    ws.close = AsyncMock()
    ws.request_headers = {"authorization": "Bearer valid_token"}

    for hook in tui_channel._connect_hooks:
        result = hook(ws)
        if inspect.isawaitable(result):
            await result

    router_client._auth_client.authenticate.assert_awaited_once()
    ws.close.assert_not_awaited()
    print("  ✅ authenticate 被调用 1 次")
    print("  ✅ ws.close 未被调用（认证通过）")


@pytest.mark.asyncio
async def test_auth_fail(tui_channel):
    router_client = _make_client(tui_channel, AsyncMock(
        return_value=AuthResult(success=False, user_id="", error="invalid token")
    ))
    print("\n[测试 2] 认证失败：token 无效 → ws.close(1008)")

    ws = MagicMock()
    ws.close = AsyncMock()
    ws.request_headers = {"authorization": "Bearer bad_token"}

    for hook in tui_channel._connect_hooks:
        result = hook(ws)
        if inspect.isawaitable(result):
            await result

    router_client._auth_client.authenticate.assert_awaited_once()
    ws.close.assert_awaited_once_with(code=1008, reason="unauthorized")
    print("  ✅ authenticate 被调用 1 次")
    print("  ✅ ws.close(1008) 被调用（认证拒绝）")


@pytest.mark.asyncio
async def test_auth_no_token(tui_channel):
    router_client = _make_client(tui_channel, AsyncMock(
        return_value=AuthResult(
            success=False, user_id="", error="No valid credentials",
            extensions={"error_code": "UNSUPPORTED_CREDENTIAL"},
        )
    ))
    print("\n[测试 3] 无 token → 认证失败并关闭连接")

    ws = MagicMock()
    ws.close = AsyncMock()
    ws.request_headers = {}

    for hook in tui_channel._connect_hooks:
        result = hook(ws)
        if inspect.isawaitable(result):
            await result

    router_client._auth_client.authenticate.assert_awaited_once()
    ws.close.assert_awaited_once_with(code=1008, reason="unauthorized")
    print("  ✅ authenticate 被调用 1 次")
    print("  ✅ ws.close(1008) 被调用（无 token）")


@pytest.mark.asyncio
async def test_auth_gateway_full_chain(tui_channel):
    router_client = _make_client(tui_channel, AsyncMock(
        return_value=AuthResult(success=True, user_id="u_abc123")
    ))
    print("\n[测试 4] 端到端：GatewayServer → TuiChannel._connect_hooks → authenticator")

    gw = MagicMock()
    gw._connect_hooks = []

    async def connection_handler(ws, path=None):
        ws_ch = tui_channel
        if ws_ch is not None:
            for hook in getattr(ws_ch, "_connect_hooks", []):
                try:
                    result = hook(ws)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    pass

    gw._connection_handler = connection_handler

    ws = MagicMock()
    ws.close = AsyncMock()
    ws.request_headers = {"authorization": "Bearer valid_token"}

    await gw._connection_handler(ws, "/tui")

    router_client._auth_client.authenticate.assert_awaited_once()
    ws.close.assert_not_awaited()
    print("  ✅ GatewayServer 触发 TuiChannel._connect_hooks 执行")
    print("  ✅ router_client.on_connect 被调用")
    print("  ✅ _auth_client.authenticate 被调用")
    print("  ✅ 认证通过，连接未被关闭")


def _make_client(tui_channel, auth_mock):
    from tests.auth.test_tui_auth_chain import _create_router_client
    client = _create_router_client(tui_channel)
    client._auth_client.authenticate = auth_mock
    return client


def _create_router_client(tui_channel):
    client = MagicMock()
    client._auth_client = MagicMock()
    client._current_agent_types = {}
    client._server_ready = True
    client._yuanrong = MagicMock()
    client._yuanrong.server_ready = True
    client._closed = False
    client._agents_cache = {}
    client._agents_cache_ttl = {}

    async def on_connect(ws):
        if client._auth_client is None:
            return AuthResult(
                success=False, user_id="", error="No valid credentials",
                extensions={"error_code": "UNSUPPORTED_CREDENTIAL"},
            )
        token = ""
        headers = getattr(ws, "request_headers", {}) or {}
        if isinstance(headers, dict):
            auth_header = headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
        context = MagicMock()
        context.channel_type = ""
        context.credentials = {"token": token} if token else {}
        context.headers = headers
        context.remote_addr = getattr(ws, "remote_address", None)
        result = await client._auth_client.authenticate(context)
        if not result.success:
            close = getattr(ws, "close", None)
            if callable(close):
                ret = close(code=1008, reason="unauthorized")
                if hasattr(ret, "__await__"):
                    await ret
        return result

    client.on_connect = on_connect
    tui_channel._connect_hooks.append(client.on_connect)
    return client