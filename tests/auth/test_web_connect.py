"""测试 web_connect.py 中 _connection_handler 调用 _handle_connect 的逻辑"""
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

from jiuwenswarm.gateway.channel_manager.web.web_connect import WebChannel, WebChannelConfig


@pytest.fixture
def mock_ws():
    """创建一个模拟的 WebSocket 对象"""
    ws = MagicMock()
    ws.path = "/ws"
    ws.remote_address = ("127.0.0.1", 54321)
    ws.closed = False
    # close 可能被 await，需要返回可 await 对象
    ws.close = AsyncMock()
    # 模拟 async for 迭代：默认空迭代，不进入消息循环体
    ws.__aiter__.return_value = iter([])
    return ws


@pytest.fixture
def web_channel():
    """创建一个 WebChannel 实例"""
    config = WebChannelConfig(
        host="0.0.0.0",
        port=8765,
        path="/ws",
    )
    router = MagicMock()
    channel = WebChannel(config, router)
    # 模拟 register_ws 和 unregister_ws 为异步空操作
    channel.register_ws = AsyncMock()
    channel.unregister_ws = AsyncMock()
    channel._connect_hooks = []
    channel._disconnect_hooks = []
    channel._ws_sessions = {}
    channel._session_busy = {}
    return channel


class TestConnectionHandlerHandleConnect:

    @patch("jiuwenswarm.gateway.channel_manager.web.web_connect._handle_connect")
    @pytest.mark.asyncio
    async def test_handle_connect_success_enters_message_loop(
        self, mock_handle_connect, web_channel, mock_ws
    ):
        """验证 _handle_connect 返回 True 时进入消息循环"""
        mock_handle_connect.return_value = True

        await web_channel._connection_handler(mock_ws, "/ws")

        mock_handle_connect.assert_awaited_once_with(mock_ws, "/ws")
        # 验证进入了消息循环（async for raw in ws）
        mock_ws.__aiter__.assert_called_once()

    @patch("jiuwenswarm.gateway.channel_manager.web.web_connect._handle_connect")
    @pytest.mark.asyncio
    async def test_handle_connect_failure_returns_early(
        self, mock_handle_connect, web_channel, mock_ws
    ):
        """验证 _handle_connect 返回 False 时直接 return，不进入消息循环"""
        mock_handle_connect.return_value = False

        await web_channel._connection_handler(mock_ws, "/ws")

        mock_handle_connect.assert_awaited_once_with(mock_ws, "/ws")
        # 验证没有进入消息循环
        mock_ws.__aiter__.assert_not_called()

    @patch("jiuwenswarm.gateway.channel_manager.web.web_connect._handle_connect")
    @pytest.mark.asyncio
    async def test_handle_connect_none_returns_early(
        self, mock_handle_connect, web_channel, mock_ws
    ):
        """验证 _handle_connect 返回 None 时直接 return（兼容 None 返回值）"""
        mock_handle_connect.return_value = None

        await web_channel._connection_handler(mock_ws, "/ws")

        mock_handle_connect.assert_awaited_once_with(mock_ws, "/ws")
        mock_ws.__aiter__.assert_not_called()

    @patch("jiuwenswarm.gateway.channel_manager.web.web_connect._handle_connect")
    @pytest.mark.asyncio
    async def test_handle_connect_passes_path_correctly(
        self, mock_handle_connect, web_channel, mock_ws
    ):
        """验证 _handle_connect 被调用时传入正确的 ws 和 path 参数"""
        mock_handle_connect.return_value = True

        await web_channel._connection_handler(mock_ws, "/ws")

        mock_handle_connect.assert_awaited_once_with(mock_ws, "/ws")