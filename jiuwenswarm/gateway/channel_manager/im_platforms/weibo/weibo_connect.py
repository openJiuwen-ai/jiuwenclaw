"""Weibo intelligent-assistant WebSocket channel."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlencode

import aiohttp

from jiuwenswarm.common.schema.message import Message, ReqMethod
from jiuwenswarm.gateway.channel_manager.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenswarm.gateway.channel_manager.im_platforms.message_text import (
    extract_human_text,
    should_skip_intermediate_message,
)

logger = logging.getLogger(__name__)

DEFAULT_WEIBO_WS_ENDPOINT = "wss://open-im.api.weibo.com/ws/stream"
DEFAULT_WEIBO_TOKEN_ENDPOINT = "https://open-im.api.weibo.com/open/auth/ws_token"


@dataclass
class WeiboChannelConfig:
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    allow_from: list[str] = field(default_factory=list)
    enable_streaming: bool = True
    ws_endpoint: str = DEFAULT_WEIBO_WS_ENDPOINT
    token_endpoint: str = DEFAULT_WEIBO_TOKEN_ENDPOINT


class WeiboChannel(BaseChannel):
    """A minimal WebSocket-based Weibo private-message channel."""

    name = "weibo"

    def __init__(self, config: WeiboChannelConfig, router: RobotMessageRouter) -> None:
        super().__init__(config, router)
        self.config = config
        self._on_message_cb: Callable[[Message], Any] | None = None
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._token = ""
        self._token_expire_at = 0.0
        self._sessions: dict[str, str] = {}

    def set_test_ws(self, ws: Any) -> None:
        """Inject a mock WebSocket for unit testing outbound sends."""
        self._ws = ws

    @property
    def channel_id(self) -> str:
        return self.name

    def on_message(self, callback: Callable[[Message], Any]) -> None:
        self._on_message_cb = callback

    async def start(self) -> None:
        if not self.config.enabled or not self.config.app_id or not self.config.app_secret:
            logger.error("WeiboChannel needs enabled, app_id, and app_secret")
            return
        if self._running:
            return
        self._running = True
        self._session = aiohttp.ClientSession()
        try:
            while self._running:
                try:
                    await self._refresh_token()
                    await self._connect_ws()
                    await self._message_loop()
                except Exception as exc:
                    logger.warning("WeiboChannel connection failed: %s", exc)
                if self._running:
                    await asyncio.sleep(5)
        finally:
            self._running = False
            await self._close_transport()

    async def stop(self) -> None:
        self._running = False
        await self._close_transport()

    async def _close_transport(self) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _refresh_token(self) -> None:
        if self._token and time.time() < self._token_expire_at - 300:
            return
        if self._session is None:
            raise RuntimeError("Weibo HTTP session is not initialized")
        async with self._session.post(
            self.config.token_endpoint,
            json={"app_id": self.config.app_id, "app_secret": self.config.app_secret},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as response:
            body = await response.json(content_type=None)
        if response.status >= 400 or body.get("code") not in (None, 0):
            raise RuntimeError(f"Weibo token request failed: {body}")
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        token = str(data.get("token") or data.get("access_token") or "")
        if not token:
            raise RuntimeError("Weibo token response has no token")
        self._token = token
        self._token_expire_at = time.time() + float(data.get("expire_in") or data.get("expires_in") or 3600)

    async def _connect_ws(self) -> None:
        if self._session is None:
            raise RuntimeError("Weibo HTTP session is not initialized")
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        params = urlencode({"app_id": self.config.app_id, "token": self._token})
        self._ws = await self._session.ws_connect(
            f"{self.config.ws_endpoint}?{params}", heartbeat=30, receive_timeout=120
        )
        logger.info("WeiboChannel connected")

    async def _message_loop(self) -> None:
        if self._ws is None:
            return
        async for frame in self._ws:
            if frame.type == aiohttp.WSMsgType.TEXT:
                await self._handle_ws_message(frame.data)
            elif frame.type == aiohttp.WSMsgType.PING:
                await self._ws.pong()
            elif frame.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR}:
                return

    async def _handle_ws_message(self, raw: str) -> None:
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return
        kind = str(body.get("type") or "")
        if kind == "ping" and self._ws is not None and not self._ws.closed:
            await self._ws.send_str(json.dumps({"type": "pong"}))
        elif kind == "message":
            payload = body.get("payload")
            if isinstance(payload, dict):
                await self.handle_inbound_message(payload)

    async def handle_inbound_message(self, payload: dict[str, Any]) -> None:
        sender = str(payload.get("from_user_id") or payload.get("fromUserId") or "")
        content = str(payload.get("text") or "")
        if not content:
            for item in payload.get("input") or []:
                if isinstance(item, dict) and item.get("text"):
                    content = str(item["text"])
                    break
        if not sender or not content or not self.is_allowed(sender):
            logger.info(
                "WeiboChannel inbound ignored: has_sender=%s has_content=%s allowed=%s payload_keys=%s",
                bool(sender),
                bool(content),
                self.is_allowed(sender) if sender else False,
                list(payload.keys())[:16],
            )
            return
        session_id = self._sessions.setdefault(sender, f"weibo_dm_{sender}")
        message_id = str(payload.get("message_id") or payload.get("messageId") or f"weibo_{int(time.time() * 1000)}")
        logger.info("WeiboChannel inbound message: sender=%s message_id=%s", sender, message_id)
        msg = Message(
            id=message_id,
            type="req",
            channel_id=self.channel_id,
            session_id=session_id,
            params={"content": content, "query": content},
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            is_stream=self.config.enable_streaming,
            metadata={"weibo_scene": "dm", "from_user_id": sender, "message_id": message_id},
        )
        if self._on_message_cb is not None:
            result = self._on_message_cb(msg)
            if inspect.isawaitable(result):
                await result
        else:
            await self.bus.route_user_message(msg)

    async def send(self, msg: Message) -> None:
        if self._ws is None or self._ws.closed:
            logger.warning("WeiboChannel send skipped: websocket not connected")
            return
        if should_skip_intermediate_message(msg):
            return
        metadata = msg.metadata or {}
        target = str(metadata.get("from_user_id") or "")
        if not target and msg.session_id and str(msg.session_id).startswith("weibo_dm_"):
            target = str(msg.session_id)[len("weibo_dm_"):]
        content = self._extract_content(msg)
        if not target or not content:
            logger.warning(
                "WeiboChannel send skipped: target=%s has_content=%s payload=%s params=%s",
                target,
                bool(content),
                msg.payload,
                msg.params,
            )
            return
        logger.info("WeiboChannel sending message: target=%s message_id=%s", target, msg.id)
        await self._ws.send_str(
            json.dumps(
                {
                    "type": "send_message",
                    "payload": {
                        "toUserId": target,
                        "text": content,
                        "messageId": msg.id,
                        "chunkId": 0,
                        "done": True,
                    },
                },
                ensure_ascii=False,
            )
        )

    @staticmethod
    def _extract_content(msg: Message) -> str:
        return extract_human_text(msg)

    def get_metadata(self) -> ChannelMetadata:
        return ChannelMetadata(channel_id=self.channel_id, source="weibo", extra={"app_id": self.config.app_id})
