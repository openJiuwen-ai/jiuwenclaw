# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import json
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from loguru import logger
from pydantic import BaseModel, Field

from jiuwenclaw.channel.base import RobotMessageRouter, BaseChannel
from jiuwenclaw.schema.message import Message, ReqMethod


class FeishuConfig(BaseModel):
    """使用WebSocket长连接的飞书/飞书通道配置。"""
    enabled: bool = False
    app_id: str = ""  # 来自飞书开放平台的应用ID
    app_secret: str = ""  # 来自飞书开放平台的应用密钥
    encrypt_key: str = ""  # 事件订阅的加密密钥（可选）
    verification_token: str = ""  # 事件订阅的验证令牌（可选）
    allow_from: list[str] = Field(default_factory=list)  # 允许的用户的open_ids


try:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        CreateMessageReactionRequest,
        CreateMessageReactionRequestBody,
        Emoji,
        P2ImMessageReceiveV1,
    )

    FEISHU_AVAILABLE = True
except ImportError:
    FEISHU_AVAILABLE = False
    lark = None
    Emoji = None

# Message type display mapping
MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}


class FeishuChannel(BaseChannel):
    """
    使用WebSocket长连接的飞书/飞书通道。
    使用WebSocket接收事件 - 不需要公网IP或webhook。
    需要：
    - 来自飞书开放平台的应用ID和应用密钥
    - 启用机器人功能
    - 启用事件订阅（im.message.receive_v1）
    """

    name = "feishu"

    def __init__(self, config: FeishuConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: FeishuConfig = config
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()  # Ordered dedup cache
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None  # 子线程内 WebSocket 用，避免与主线程 loop 冲突
        self._gateway_callback: Callable[[Message], None] | None = None

    @property
    def channel_id(self) -> str:
        """ChannelManager 按 channel_id 注册与派发."""
        return self.name

    def on_message(self, callback: Callable[[Message], None]) -> None:
        """ChannelManager 注册：收到消息时调用 callback，不再写 router（Gateway 路径）."""
        self._gateway_callback = callback

    async def _handle_message(
        self,
        chat_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """若已通过 on_message 注册网关回调，则直接回调；否则走 router.publish_user_messages（如 demo_feishu）."""
        msg = Message(
            id=chat_id,
            type="req",
            channel_id=self.name,
            session_id=str(chat_id),
            params={"content": content, "query": content},
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            metadata=metadata,
        )
        if self._gateway_callback:
            self._gateway_callback(msg)
        else:
            await self.bus.route_user_message(msg)

    async def start(self) -> None:
        """使用WebSocket长连接启动飞书机器人"""
        if not FEISHU_AVAILABLE:
            logger.error("Feishu SDK not installed. Run: pip install lark-oapi")
            return

        if not self.config.app_id or not self.config.app_secret:
            logger.error("Feishu app_id and app_secret not configured")
            return

        self._running = True
        self._loop = asyncio.get_running_loop()

        # 创建用于发送消息的Lark客户端
        self._client = lark.Client.builder() \
            .app_id(self.config.app_id) \
            .app_secret(self.config.app_secret) \
            .log_level(lark.LogLevel.INFO) \
            .build()

        # 在子线程内创建并启动 WebSocket Client，避免 lark 内部 asyncio 绑定主线程 loop（"This event loop is already running"）
        app_id = self.config.app_id
        app_secret = self.config.app_secret
        encrypt_key = self.config.encrypt_key or ""
        verification_token = self.config.verification_token or ""
        on_message_sync = self._on_message_sync

        def run_ws():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._ws_loop = loop
            # lark_oapi.ws.client 使用模块级全局 loop，start() 里 run_until_complete(_connect/_disconnect) 会报 "already running"
            # 且 _connect/_disconnect 未被 await 会触发 RuntimeWarning；在子线程内临时替换为该线程的 loop
            import lark_oapi.ws.client as _ws_client_mod
            _saved_loop = getattr(_ws_client_mod, "loop", None)
            _ws_client_mod.loop = loop
            ws_client = None
            try:
                event_handler = lark.EventDispatcherHandler.builder(
                    encrypt_key,
                    verification_token,
                ).register_p2_im_message_receive_v1(on_message_sync).build()
                ws_client = lark.ws.Client(
                    app_id,
                    app_secret,
                    event_handler=event_handler,
                    log_level=lark.LogLevel.INFO,
                )
                self._ws_client = ws_client
                ws_client.start()
            except Exception as e:
                logger.error("Feishu WebSocket error: %s", e)
            finally:
                if _saved_loop is not None:
                    _ws_client_mod.loop = _saved_loop
                if ws_client is None:
                    self._ws_client = None
                try:
                    loop.run_until_complete(asyncio.sleep(0.25))
                except Exception:
                    pass
                try:
                    loop.close()
                except Exception:
                    pass
                self._ws_loop = None

        self._ws_client = None
        self._ws_thread = threading.Thread(target=run_ws, daemon=True)
        self._ws_thread.start()
        # 等子线程完成 Client 创建再继续，避免 stop() 时 _ws_client 仍为 None
        for _ in range(50):
            if self._ws_client is not None:
                break
            await asyncio.sleep(0.1)

        logger.info("Feishu bot started with WebSocket long connection")
        logger.info("No public IP required - using WebSocket to receive events")

        # 持续运行直到停止
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """停止飞书机器人"""
        self._running = False
        if self._ws_client:
            try:
                self._ws_client.stop()
            except Exception as e:
                logger.warning("Error stopping WebSocket client: %s", e)
        if self._ws_loop and self._ws_loop.is_running():
            self._ws_loop.call_soon_threadsafe(self._ws_loop.stop)
        logger.info("Feishu bot stopped")

    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> None:
        """添加反应的同步助手（在线程池中运行）"""
        try:
            request = CreateMessageReactionRequest.builder() \
                .message_id(message_id) \
                .request_body(
                CreateMessageReactionRequestBody.builder()
                .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                .build()
            ).build()

            response = self._client.im.v1.message_reaction.create(request)

            if not response.success():
                logger.warning(f"Failed to add reaction: code={response.code}, msg={response.msg}")
            else:
                logger.debug(f"Added {emoji_type} reaction to message {message_id}")
        except Exception as e:
            logger.warning(f"Error adding reaction: {e}")

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """
        为消息添加反应表情符号（非阻塞）。
        常见表情符号类型：THUMBSUP、OK、EYES、DONE、OnIt、HEART
        """
        if not self._client or not Emoji:
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._add_reaction_sync, message_id, emoji_type)

    # 匹配markdown表格的正则表达式（标题+分隔符+数据行）
    _TABLE_RE = re.compile(
        r"((?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|[-:\s|]+\|[ \t]*\n)(?:^[ \t]*\|.+\|[ \t]*\n?)+)",
        re.MULTILINE,
    )

    @staticmethod
    def _parse_md_table(table_text: str) -> dict | None:
        """将markdown表格解析为飞书表格元素"""
        lines = [l.strip() for l in table_text.strip().split("\n") if l.strip()]
        if len(lines) < 3:
            return None
        split = lambda l: [c.strip() for c in l.strip("|").split("|")]
        headers = split(lines[0])
        rows = [split(l) for l in lines[2:]]
        columns = [{"tag": "column", "name": f"c{i}", "display_name": h, "width": "auto"}
                   for i, h in enumerate(headers)]
        return {
            "tag": "table",
            "page_size": len(rows) + 1,
            "columns": columns,
            "rows": [{f"c{i}": r[i] if i < len(r) else "" for i in range(len(headers))} for r in rows],
        }

    def _build_card_elements(self, content: str) -> list[dict]:
        """将内容分割为markdown+表格元素用于飞书卡片"""
        elements, last_end = [], 0
        for m in self._TABLE_RE.finditer(content):
            before = content[last_end:m.start()].strip()
            if before:
                elements.append({"tag": "markdown", "content": before})
            elements.append(self._parse_md_table(m.group(1)) or {"tag": "markdown", "content": m.group(1)})
            last_end = m.end()
        remaining = content[last_end:].strip()
        if remaining:
            elements.append({"tag": "markdown", "content": remaining})
        return elements or [{"tag": "markdown", "content": content}]

    async def send(self, msg: Message) -> None:
        """通过飞书发送消息"""
        if not self._client:
            logger.warning("Feishu client not initialized")
            return

        try:
            # receive_id：优先用 session_id（回复对象），否则 id（Gateway 出站可能用 request_id 填 id）
            receive_id = getattr(msg, "session_id", None) or msg.id
            # 飞书 API：群聊 oc_ 用 chat_id，用户 ou_ 用 open_id（用 id 会报 99992402 field validation failed）
            if receive_id.startswith("oc_"):
                receive_id_type = "chat_id"
            else:
                receive_id_type = "open_id"

            # 内容：Gateway/Agent 响应在 payload.content，直接发可能在 params.content
            content_str = (msg.params or {}).get("content") or (getattr(msg, "payload") or {}).get("content") or ""
            #修改九问输出格式，确保内容为字符串
            if isinstance(content_str, dict):
                content_str = content_str.get("output", str(content_str))
            content_str = str(content_str)
            if not content_str.strip():
                logger.warning("Feishu send: content 为空，跳过发送")
                return

            elements = self._build_card_elements(content_str)
            card = {
                "config": {"wide_screen_mode": True},
                "elements": elements,
            }
            content = json.dumps(card, ensure_ascii=False)

            request = CreateMessageRequest.builder() \
                .receive_id_type(receive_id_type) \
                .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(receive_id)
                .msg_type("interactive")
                .content(content)
                .build()
            ).build()

            response = self._client.im.v1.message.create(request)

            if not response.success():
                logger.error(
                    f"Failed to send Feishu message: code={response.code}, "
                    f"msg={response.msg}, log_id={response.get_log_id()}"
                )
            else:
                logger.debug(f"Feishu message sent to {msg.id}")

        except Exception as e:
            logger.error(f"Error sending Feishu message: {e}")

    def _on_message_sync(self, data: "P2ImMessageReceiveV1") -> None:
        """
        传入消息的同步处理器（从WebSocket线程调用）。
        在主事件循环中调度异步处理。
        """
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message(data), self._loop)

    async def _on_message(self, data: "P2ImMessageReceiveV1") -> None:
        """处理来自飞书的传入消息"""
        try:
            event = data.event
            message = event.message
            sender = event.sender
            # Deduplication check
            message_id = message.message_id
            if message_id in self._processed_message_ids:
                return
            self._processed_message_ids[message_id] = None

            # Trim cache: keep most recent 500 when exceeds 1000
            while len(self._processed_message_ids) > 1000:
                self._processed_message_ids.popitem(last=False)

            # Skip bot messages
            sender_type = sender.sender_type
            if sender_type == "bot":
                return

            chat_id = message.chat_id
            chat_type = message.chat_type  # "p2p" or "group"
            msg_type = message.message_type

            # 后台加「已读」反应，不阻塞消息处理与回复
            asyncio.create_task(self._add_reaction(message_id, "THUMBSUP"))

            # Parse message content
            if msg_type == "text":
                try:
                    content = json.loads(message.content).get("text", "")
                except json.JSONDecodeError:
                    content = message.content or ""
            else:
                content = MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]")

            if not content:
                return

            open_id = getattr(getattr(sender, "sender_id", None), "open_id", None) or ""
            await self._handle_message(
                chat_id=chat_id,
                content=content,
                metadata={
                    "message_id": message_id,
                    "chat_type": chat_type,
                    "msg_type": msg_type,
                    "open_id": open_id,
                },
            )

        except Exception as e:
            logger.error(f"Error processing Feishu message: {e}")
