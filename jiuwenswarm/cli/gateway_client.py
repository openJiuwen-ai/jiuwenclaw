# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Gateway `/tui`-compatible WebSocket client for CLI."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _connect_ws(url: str):
    try:
        from websockets.legacy.client import connect as legacy_connect
        return legacy_connect(url, close_timeout=2.0, max_size=8 * 2**20)
    except ImportError:
        import websockets
        return websockets.connect(url, close_timeout=2.0, max_size=8 * 2**20)


class GatewayClient:
    def __init__(self, url: str) -> None:
        self._url = url
        self._ws: Any = None

    @property
    def url(self) -> str:
        return self._url

    async def connect(self) -> None:
        self._ws = await _connect_ws(self._url)

        try:
            raw = await self._ws.recv()
        except Exception:
            await self._ws.close()
            self._ws = None
            raise ConnectionError(
                f"Gateway closed connection before sending ack: {self._url}"
            ) from None

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await self._ws.close()
            self._ws = None
            raise ConnectionError(
                f"Gateway sent invalid ack frame: {self._url}"
            ) from None

        if data.get("type") != "event" or data.get("event") != "connection.ack":
            await self._ws.close()
            self._ws = None
            raise ConnectionError(
                f"Expected connection.ack, got: {data.get('type')}/{data.get('event')}"
            ) from None

        logger.debug("[GatewayClient] connected: %s", self._url)

    async def send_request(self, frame: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(frame, ensure_ascii=False))

    async def recv(self) -> dict[str, Any]:
        raw = await self._ws.recv()
        return json.loads(raw)

    async def close(self) -> None:
        if self._ws is not None:
            ws = self._ws
            self._ws = None
            await ws.close()

    def set_mock_ws(self, ws: Any) -> None:
        self._ws = ws
