import asyncio
import pytest
from unittest.mock import AsyncMock

from jiuwenclaw.e2a.link_heartbeat import build_link_heartbeat_wire
from jiuwenclaw.gateway.open_ability_client import OpenAbilityWebSocketClient


class FailingWebSocket:
    def __init__(self) -> None:
        self.close_called = False

    async def recv(self):
        raise RuntimeError("boom")

    async def close(self) -> None:
        self.close_called = True


@pytest.mark.asyncio
async def test_receiver_loop_runtime_error_does_not_trigger_reconnect() -> None:
    client = OpenAbilityWebSocketClient("sb-1")
    ws = FailingWebSocket()
    connection_lost_handler = AsyncMock()

    client._ws = ws
    client._uri = "ws://127.0.0.1:9001/ws"
    client._running = True
    client._server_ready = True
    client.set_connection_lost_handler(connection_lost_handler)

    await client._message_receiver_loop()

    connection_lost_handler.assert_not_awaited()
    assert ws.close_called is True
    assert client._ws is None
    assert client._uri is None
    assert client.server_ready is False


@pytest.mark.asyncio
async def test_receiver_loop_dispatches_link_heartbeat_without_server_push() -> None:
    wire = build_link_heartbeat_wire(sandbox_id="sb-hb")
    link_handler = AsyncMock()
    client = OpenAbilityWebSocketClient("sb-hb")
    client.set_link_heartbeat_handler(link_handler)

    assert client._dispatch_link_heartbeat(wire) is True
    await asyncio.sleep(0)
    link_handler.assert_awaited_once_with(wire)