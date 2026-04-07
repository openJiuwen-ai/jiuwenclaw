# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for WebSocketAgentServerClient disconnect callback.

Tests focus on public API behavior without accessing protected members.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.gateway.agent_client import WebSocketAgentServerClient


def _create_mock_ws_for_disconnect(
    *, recv_side_effect: Exception | list | None = None
) -> MagicMock:
    """Create a mock WebSocket for disconnect testing."""
    mock_ws = MagicMock()
    mock_ws.recv = AsyncMock(side_effect=recv_side_effect)
    mock_ws.close = AsyncMock()
    mock_ws.send = AsyncMock()
    return mock_ws


class TestDisconnectCallback:
    """Test disconnect callback behavior via public API."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_callback_triggered_on_exception():
        """Test that callback is triggered when exception occurs in receiver loop."""
        client = WebSocketAgentServerClient()
        callback = AsyncMock()
        client.set_disconnect_handler(callback, session_id="session-abc")

        mock_ws = _create_mock_ws_for_disconnect(
            recv_side_effect=Exception("Connection lost")
        )

        async def mock_connect(*args, **kwargs):
            return mock_ws

        with patch("websockets.legacy.client.connect", side_effect=mock_connect):
            await client.connect("ws://test")
            await asyncio.sleep(0.1)

        callback.assert_awaited_once_with("session-abc")

    @staticmethod
    @pytest.mark.asyncio
    async def test_callback_exception_is_caught():
        """Test that callback exception is caught and doesn't propagate."""

        async def failing_callback(session_id: str):
            raise ValueError("Callback error")

        client = WebSocketAgentServerClient()
        client.set_disconnect_handler(failing_callback, session_id="test")

        mock_ws = _create_mock_ws_for_disconnect(
            recv_side_effect=Exception("Connection lost")
        )

        async def mock_connect(*args, **kwargs):
            return mock_ws

        with patch("websockets.legacy.client.connect", side_effect=mock_connect):
            await client.connect("ws://test")
            await asyncio.sleep(0.1)

    @staticmethod
    @pytest.mark.asyncio
    async def test_no_callback_when_handler_is_none():
        """Test that no callback is triggered when handler is None."""
        client = WebSocketAgentServerClient()
        client.set_disconnect_handler(None)

        mock_ws = _create_mock_ws_for_disconnect(
            recv_side_effect=Exception("Connection lost")
        )

        async def mock_connect(*args, **kwargs):
            return mock_ws

        with patch("websockets.legacy.client.connect", side_effect=mock_connect):
            await client.connect("ws://test")
            await asyncio.sleep(0.1)


class TestDisconnectMethod:
    """Test disconnect() public method behavior."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_disconnect_does_not_trigger_callback():
        """Test that disconnect() does not trigger disconnect callback."""
        client = WebSocketAgentServerClient()
        callback = AsyncMock()
        client.set_disconnect_handler(callback, session_id="test-session")

        mock_ws = _create_mock_ws_for_disconnect(recv_side_effect=asyncio.TimeoutError)

        async def mock_connect(*args, **kwargs):
            return mock_ws

        with patch("websockets.legacy.client.connect", side_effect=mock_connect):
            await client.connect("ws://test")

        callback.reset_mock()
        await client.disconnect()

        callback.assert_not_awaited()

    @staticmethod
    @pytest.mark.asyncio
    async def test_disconnect_idempotent():
        """Test that disconnect() can be called multiple times safely."""
        client = WebSocketAgentServerClient()
        callback = AsyncMock()
        client.set_disconnect_handler(callback, session_id="test-session")

        mock_ws = _create_mock_ws_for_disconnect(recv_side_effect=asyncio.TimeoutError)

        async def mock_connect(*args, **kwargs):
            return mock_ws

        with patch("websockets.legacy.client.connect", side_effect=mock_connect):
            await client.connect("ws://test")

        callback.reset_mock()
        await client.disconnect()
        await client.disconnect()

        callback.assert_not_awaited()

    @staticmethod
    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected():
        """Test that disconnect() works when client is not connected."""
        client = WebSocketAgentServerClient()
        callback = AsyncMock()
        client.set_disconnect_handler(callback, session_id="test-session")

        await client.disconnect()

        callback.assert_not_awaited()


class TestServerReady:
    """Test server_ready public property."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_server_ready_after_connection_ack():
        """Test that server_ready is True after receiving connection.ack."""
        client = WebSocketAgentServerClient()

        connection_ack = json.dumps({"type": "event", "event": "connection.ack"})

        mock_ws = MagicMock()
        mock_ws.recv = AsyncMock(side_effect=[connection_ack, asyncio.CancelledError()])
        mock_ws.close = AsyncMock()
        mock_ws.send = AsyncMock()

        async def mock_connect(*args, **kwargs):
            return mock_ws

        with patch("websockets.legacy.client.connect", side_effect=mock_connect):
            await client.connect("ws://test")

        assert client.server_ready is True

    @staticmethod
    def test_server_ready_false_initially():
        """Test that server_ready is False initially."""
        client = WebSocketAgentServerClient()
        assert client.server_ready is False
