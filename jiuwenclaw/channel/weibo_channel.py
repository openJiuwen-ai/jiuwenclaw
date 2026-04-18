# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""WeiboChannel - 微博私信通道实现.

基于微博开放平台智能助手 WebSocket 协议。
通过 AppId + AppSecret 获取 token，再通过 WebSocket 收发私信消息。

协议参考: https://github.com/wecode-ai/openclaw-weibo
API 域名: open-im.api.weibo.com

消息流:
  1. POST /open/auth/ws_token  → 获取 WebSocket token
  2. WSS  /ws/stream?app_id=xxx&token=xxx  → 建立长连接
  3. 收消息: {"type": "message", "payload": {...}}
  4. 发消息: {"type": "send_message", "payload": {"toUserId": "...", "text": "..."}}
  5. 心跳:   30s ping / 120s pong 超时
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlencode

import logging

import aiohttp

from jiuwenclaw.channel.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenclaw.schema.message import Message, ReqMethod

logger = logging.getLogger(__name__)

# 微博开放平台默认端点
_DEFAULT_WS_ENDPOINT = "wss://open-im.api.weibo.com/ws/stream"
_DEFAULT_TOKEN_ENDPOINT = "https://open-im.api.weibo.com/open/auth/ws_token"

# 心跳与超时
_HEARTBEAT_INTERVAL = 30  # 秒
_PONG_TIMEOUT = 120  # 秒
_TOKEN_EXPIRE_MARGIN = 300  # token 过期前 5 分钟刷新
_RECONNECT_DELAY = 5  # 断连重试间隔（秒）


@dataclass
class WeiboChannelConfig:
    """微博私信通道配置."""

    enabled: bool = False
    app_id: str = ""  # 微博开放平台 AppId（通过 @微博龙虾助手 获取）
    app_secret: str = ""  # 微博开放平台 AppSecret
    allow_from: list[str] = field(default_factory=list)
    enable_streaming: bool = True
    # 端点（可覆盖）
    ws_endpoint: str = _DEFAULT_WS_ENDPOINT
    token_endpoint: str = _DEFAULT_TOKEN_ENDPOINT


class WeiboChannel(BaseChannel):
    """微博私信通道.

    使用微博开放平台 WebSocket 协议接收和发送私信。
    自动处理 token 刷新、心跳保活、断连重连。

    需要:
    - 微博开放平台 AppId + AppSecret
    - (私信 @微博龙虾助手 发送 "连接龙虾" 获取)
    """

    name = "weibo"

    def __init__(self, config: WeiboChannelConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: WeiboChannelConfig = config
        self._running = False
        self._on_message_cb: Callable[[Message], Any] | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._token: str = ""
        self._token_expire_at: float = 0.0
        self._sessions: dict[str, str] = {}  # user_id -> session_id
        self._tasks: list[asyncio.Task] = []

    @property
    def channel_id(self) -> str:
        return self.name

    @property
    def clients(self) -> set[Any]:
        return set()

    @property
    def token(self) -> str:
        return self._token

    @property
    def token_expire_at(self) -> float:
        return self._token_expire_at

    @property
    def sessions(self) -> dict[str, str]:
        return self._sessions

    @property
    def tasks(self) -> list[asyncio.Task]:
        return self._tasks

    @property
    def message_callback(self) -> Callable[[Message], Any] | None:
        return self._on_message_cb

    def on_message(self, callback: Callable[[Message], None]) -> None:
        self._on_message_cb = callback

    async def start(self) -> None:
        """启动微博私信通道."""
        if not self.config.enabled:
            logger.warning("WeiboChannel 未启用（enabled=False）")
            return

        if not self.config.app_id or not self.config.app_secret:
            logger.error("WeiboChannel 缺少 app_id 或 app_secret 配置")
            return

        if self._running:
            logger.warning("WeiboChannel 已在运行")
            return

        self._running = True
        self._session = aiohttp.ClientSession()

        logger.info(
            "WeiboChannel 启动中 (app_id: %s...)", self.config.app_id[:8]
        )

        try:
            await self._connect_loop()
        except Exception as e:
            logger.error("WeiboChannel 运行异常: %s", e)
        finally:
            self._running = False

    async def _connect_loop(self) -> None:
        """主连接循环: 获取 token → 连接 WebSocket → 收发消息 → 断连重连."""
        while self._running:
            try:
                # 1. 获取/刷新 token
                await self._refresh_token()

                # 2. 连接 WebSocket
                await self._connect_ws()

                # 3. 进入消息循环（内部会阻塞直到断连）
                await self._message_loop()

            except Exception as e:
                logger.error("WeiboChannel 连接异常: %s", e)

            if self._running:
                logger.info("WeiboChannel %d 秒后重连...", _RECONNECT_DELAY)
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _refresh_token(self) -> None:
        """从微博 API 获取 WebSocket token."""
        # 如果 token 还未过期（留出余量），不刷新
        if self._token and time.time() < self._token_expire_at - _TOKEN_EXPIRE_MARGIN:
            return

        payload = {
            "app_id": self.config.app_id,
            "app_secret": self.config.app_secret,
        }

        async with self._session.post(
            self.config.token_endpoint,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            body = await resp.json()

        if body.get("code") != 0:
            raise RuntimeError(
                f"微博 token 获取失败: code={body.get('code')} msg={body.get('message')}"
            )

        data = body.get("data", {})
        self._token = data["token"]
        expire_in = data.get("expire_in", 3600)
        self._token_expire_at = time.time() + expire_in

        logger.info(
            "WeiboChannel token 已获取，有效期 %d 秒",
            expire_in,
        )

    async def _connect_ws(self) -> None:
        """建立 WebSocket 连接."""
        # 关闭旧连接
        if self._ws and not self._ws.closed:
            await self._ws.close()

        params = urlencode({"app_id": self.config.app_id, "token": self._token})
        url = f"{self.config.ws_endpoint}?{params}"

        self._ws = await self._session.ws_connect(
            url,
            heartbeat=_HEARTBEAT_INTERVAL,
            receive_timeout=_PONG_TIMEOUT,
        )

        logger.info("WeiboChannel WebSocket 已连接")

    async def _message_loop(self) -> None:
        """WebSocket 消息接收循环."""
        if self._ws is None:
            return

        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._handle_ws_message(msg.data)
            elif msg.type == aiohttp.WSMsgType.PING:
                await self._ws.pong()
            elif msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
                aiohttp.WSMsgType.CLOSING,
            ):
                logger.warning("WeiboChannel WebSocket 关闭: type=%s", msg.type)
                break

        # 如果不是主动停止，触发重连
        if self._running:
            logger.warning("WeiboChannel WebSocket 断开，将重连")

    async def _handle_ws_message(self, raw: str) -> None:
        """处理单条 WebSocket 消息."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("WeiboChannel 收到非 JSON 消息: %s", raw[:200])
            return

        msg_type = data.get("type")

        if msg_type == "message":
            await self._on_inbound_message(data.get("payload", {}))
        elif msg_type == "ping":
            # 服务端 ping，回复 pong
            if self._ws and not self._ws.closed:
                await self._ws.send_str(json.dumps({"type": "pong"}))
        elif msg_type == "system":
            logger.info("WeiboChannel 系统消息: %s", data.get("payload", {}).get("message"))
        elif msg_type == "ack":
            pass  # 发送确认，忽略
        elif msg_type == "error":
            logger.error("WeiboChannel 收到错误: %s", data.get("payload", {}))
        else:
            logger.debug("WeiboChannel 未处理的消息类型: %s", msg_type)

    async def _on_inbound_message(self, payload: dict) -> None:
        """处理微博私信入站消息."""
        try:
            from_user_id = str(payload.get("from_user_id", "") or payload.get("fromUserId", ""))
            text = payload.get("text", "")
            message_id = str(payload.get("message_id", "") or payload.get("messageId", ""))
            timestamp = payload.get("timestamp", 0) or int(time.time() * 1000)

            # 检查输入类型（可能包含富媒体）
            inputs = payload.get("input") or []
            if not text and inputs:
                # 从 input 中提取文本
                for item in inputs:
                    if isinstance(item, dict) and item.get("text"):
                        text = item["text"]
                        break

            if not from_user_id:
                logger.warning("WeiboChannel 消息缺少 from_user_id，跳过")
                return

            if not self.is_allowed(from_user_id):
                logger.warning("WeiboChannel 消息来自未授权用户: %s", from_user_id)
                return

            # 构建 session_id
            session_key = f"weibo_{from_user_id}"
            session_id = self._sessions.get(session_key)
            if not session_id:
                session_id = f"weibo_dm_{from_user_id}"
                self._sessions[session_key] = session_id

            user_msg = Message(
                id=message_id or f"wb_{int(time.time()*1000)}",
                type="req",
                channel_id=self.channel_id,
                session_id=session_id,
                params={"content": text, "query": text},
                timestamp=timestamp / 1000 if timestamp > 1e12 else timestamp,
                ok=True,
                req_method=ReqMethod.CHAT_SEND,
                metadata={
                    "from_user_id": from_user_id,
                    "message_id": message_id,
                    "weibo_scene": "dm",
                },
            )

            if self._on_message_cb:
                result = self._on_message_cb(user_msg)
                if asyncio.iscoroutine(result):
                    await result
            else:
                await self.bus.route_user_message(user_msg)

            logger.info(
                "WeiboChannel 收到私信: user=%s text=%s",
                from_user_id, text[:50],
            )

        except Exception as e:
            logger.error("WeiboChannel 处理入站消息异常: %s", e)

    async def send(self, msg: Message) -> None:
        """通过微博 WebSocket 发送私信."""
        if not self._ws or self._ws.closed:
            logger.warning("WeiboChannel WebSocket 未连接，无法发送")
            return

        try:
            metadata = msg.metadata or {}
            to_user_id = metadata.get("from_user_id", "")

            # 从 session_id 解析 user_id（兜底）
            if not to_user_id and msg.session_id and msg.session_id.startswith("weibo_dm_"):
                to_user_id = msg.session_id[len("weibo_dm_"):]

            if not to_user_id:
                logger.warning("WeiboChannel send: 缺少 to_user_id")
                return

            content = self.extract_content(msg)
            if not content:
                return

            send_payload = {
                "type": "send_message",
                "payload": {
                    "toUserId": to_user_id,
                    "text": content,
                    "messageId": msg.id,
                    "chunkId": 0,
                    "done": True,
                },
            }

            await self._ws.send_str(json.dumps(send_payload, ensure_ascii=False))
            logger.debug("WeiboChannel 已发送私信给 %s", to_user_id)

        except Exception as e:
            logger.error("WeiboChannel 发送消息失败: %s", e)

    async def stop(self) -> None:
        """停止微博私信通道."""
        self._running = False

        # 取消后台任务
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

        if self._ws and not self._ws.closed:
            await self._ws.close()
            self._ws = None

        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

        logger.info("WeiboChannel 已停止")

    @staticmethod
    def extract_content(msg: Message) -> str:
        """从 Message 中提取文本内容."""
        content = (
            (msg.params or {}).get("content")
            or (getattr(msg, "payload") or {}).get("content")
            or ""
        )
        if isinstance(content, dict):
            content = content.get("output", str(content))
        return str(content).strip()

    def get_metadata(self) -> ChannelMetadata:
        """获取 Channel 元数据."""
        return ChannelMetadata(
            channel_id=self.channel_id,
            source="weibo",
            extra={
                "app_id": self.config.app_id,
                "ws_endpoint": self.config.ws_endpoint,
            },
        )
