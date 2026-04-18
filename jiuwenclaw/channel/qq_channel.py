# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""QQChannel - QQ 机器人通道实现.

支持 QQ 频道（Guild）、QQ 群聊（Group）、QQ 单聊（C2C）三种场景。
使用 QQ 官方开放平台 Python SDK botpy (pip install qq-botpy)。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import logging

from jiuwenclaw.channel.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenclaw.schema.message import Message, ReqMethod

logger = logging.getLogger(__name__)

try:
    import botpy
    from botpy.message import GroupMessage, Message as BotpyMessage

    BOTPY_AVAILABLE = True
except ImportError:
    BOTPY_AVAILABLE = False
    botpy = None  # type: ignore[assignment]
    BotpyMessage = None  # type: ignore[assignment,misc]
    GroupMessage = None  # type: ignore[assignment,misc]


@dataclass
class QQChannelConfig:
    """QQ 通道配置."""

    enabled: bool = False
    app_id: str = ""  # QQ 开放平台 AppID
    app_secret: str = ""  # QQ 开放平台 AppSecret
    allow_from: list[str] = field(default_factory=list)
    enable_streaming: bool = True
    # 场景开关
    enable_guild: bool = True  # 频道场景
    enable_group: bool = True  # 群聊场景
    enable_c2c: bool = True  # 单聊场景


if BOTPY_AVAILABLE:

    class _QQBotpyClient(botpy.Client):  # type: ignore[misc]
        """botpy.Client 子类，用于接收 QQ 事件并转发给 QQChannel.

        botpy v1.1.5+ 通过 appid + secret 鉴权，client.run(appid=..., secret=...)。
        """

        def __init__(self, qq_channel: "QQChannel", intents: Any):
            super().__init__(intents=intents)
            self._qq_channel = qq_channel

        # ---- 频道事件 ----

        async def on_at_message_create(self, message: BotpyMessage) -> None:
            """频道中 @机器人 消息."""
            if not self._qq_channel.config.enable_guild:
                return
            await self._qq_channel.handle_guild_message(message)

        async def on_public_message_delete(self, message: BotpyMessage) -> None:
            pass

        # ---- 群聊事件 ----

        async def on_group_at_message_create(self, message: GroupMessage) -> None:
            """群聊中 @机器人 消息."""
            if not self._qq_channel.config.enable_group:
                return
            await self._qq_channel.handle_group_message(message)

        # ---- 单聊事件 ----

        async def on_c2c_message_create(self, message: Any) -> None:
            """单聊私信消息."""
            if not self._qq_channel.config.enable_c2c:
                return
            await self._qq_channel.handle_c2c_message(message)

else:
    _QQBotpyClient = None  # type: ignore[assignment,misc]


class QQChannel(BaseChannel):
    """QQ 机器人通道.

    使用 QQ 官方 botpy SDK 接入，支持:
    - 频道（Guild）@消息
    - 群聊（Group）@消息
    - 单聊（C2C）私信

    需要:
    - QQ 开放平台 AppID + AppSecret
    - pip install qq-botpy
    """

    name = "qq"

    def __init__(self, config: QQChannelConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: QQChannelConfig = config
        self._botpy_client: _QQBotpyClient | None = None
        self._running = False
        self._on_message_cb: Callable[[Message], Any] | None = None
        # 缓存 session_id: 消息来源 key -> session_id
        self._sessions: dict[str, str] = {}

    @property
    def channel_id(self) -> str:
        return self.name

    @property
    def clients(self) -> set[Any]:
        return set()

    @property
    def sessions(self) -> dict[str, str]:
        return self._sessions

    @property
    def message_callback(self) -> Callable[[Message], Any] | None:
        return self._on_message_cb

    def on_message(self, callback: Callable[[Message], None]) -> None:
        self._on_message_cb = callback

    async def start(self) -> None:
        """启动 QQ 机器人."""
        if not BOTPY_AVAILABLE:
            logger.error(
                "qq-botpy SDK not installed. Run: pip install qq-botpy"
            )
            return

        if not self.config.enabled:
            logger.warning("QQChannel 未启用（enabled=False）")
            return

        if not self.config.app_id or not self.config.app_secret:
            logger.error("QQChannel 缺少 app_id 或 app_secret 配置")
            return

        if self._running:
            logger.warning("QQChannel 已在运行")
            return

        self._running = True

        try:
            # 构建 Intents
            intents = botpy.Intents.none()
            if self.config.enable_guild:
                intents.public_guild_messages = True
            if self.config.enable_group:
                intents.group_at_message = True
            if self.config.enable_c2c:
                intents.c2c_message = True

            # 创建 botpy 客户端
            self._botpy_client = _QQBotpyClient(self, intents=intents)

            # botpy.run() 是阻塞的，放在后台任务中
            asyncio.create_task(
                self._run_botpy(),
                name="qq-botpy-run",
            )

            logger.info(
                "QQChannel 启动中 (app_id: %s...)", self.config.app_id[:8]
            )

            # 等待 botpy 连接就绪
            while self._running:
                await asyncio.sleep(1)

        except Exception as e:
            logger.error("QQChannel 启动失败: %s", e)
            self._running = False
            raise

    async def _run_botpy(self) -> None:
        """在后台运行 botpy client."""
        try:
            await self._botpy_client.run(
                appid=self.config.app_id,
                secret=self.config.app_secret,
            )
        except Exception as e:
            logger.error("QQ botpy client 运行异常: %s", e)
            self._running = False

    async def stop(self) -> None:
        """停止 QQ 机器人."""
        self._running = False
        if self._botpy_client:
            try:
                # botpy 没有显式 stop API，设置标志让其自动退出
                self._botpy_client.close()
            except Exception as e:
                logger.warning("Error stopping QQ botpy client: %s", e)
        logger.info("QQChannel 已停止")

    async def send(self, msg: Message) -> None:
        """通过 QQ 发送消息."""
        if not self._botpy_client or not self._running:
            logger.warning("QQ botpy client 未初始化或未运行")
            return

        try:
            metadata = msg.metadata or {}
            scene = metadata.get("qq_scene", "guild")
            content = self.extract_content(msg)
            if not content:
                return

            if scene == "group":
                # 群聊消息
                group_openid = metadata.get("group_openid")
                if not group_openid:
                    logger.warning("QQ send: 缺少 group_openid")
                    return
                await self._botpy_client.api.post_group_message(
                    group_openid=group_openid,
                    msg_type=0,  # 文本消息
                    content={"text": content},
                )
            elif scene == "c2c":
                # 单聊消息
                user_openid = metadata.get("user_openid")
                if not user_openid:
                    logger.warning("QQ send: 缺少 user_openid")
                    return
                await self._botpy_client.api.post_c2c_message(
                    openid=user_openid,
                    msg_type=0,
                    content={"text": content},
                )
            else:
                # 频道消息（默认）
                channel_id = metadata.get("channel_id")
                if not channel_id:
                    logger.warning("QQ send: 缺少 channel_id")
                    return
                await self._botpy_client.api.post_message(
                    channel_id=channel_id,
                    content=content,
                )

            logger.debug("QQ message sent, scene=%s", scene)

        except Exception as e:
            logger.error("QQ send 失败: %s", e)

    # ---- 内部消息处理 ----

    async def handle_guild_message(self, message: BotpyMessage) -> None:
        """处理频道 @消息."""
        try:
            author_id = str(message.author.id)
            if not self.is_allowed(author_id):
                logger.warning("QQ 频道消息来自未授权用户: %s", author_id)
                return

            # 去掉 @机器人 部分
            content = self.strip_at_mention(message.content or "", message.mentions)

            session_key = f"guild_{message.guild_id}_{message.channel_id}"
            session_id = self._sessions.get(session_key)
            if not session_id:
                session_id = f"qq_guild_{message.guild_id}_{message.channel_id}"
                self._sessions[session_key] = session_id

            user_msg = Message(
                id=message.id,
                type="req",
                channel_id=self.channel_id,
                session_id=session_id,
                params={"content": content, "query": content},
                timestamp=time.time(),
                ok=True,
                req_method=ReqMethod.CHAT_SEND,
                metadata={
                    "qq_scene": "guild",
                    "guild_id": message.guild_id,
                    "channel_id": message.channel_id,
                    "user_id": author_id,
                    "message_id": message.id,
                },
            )

            await self._dispatch(user_msg)

            logger.info(
                "QQ 频道消息: guild=%s channel=%s user=%s text=%s",
                message.guild_id, message.channel_id, author_id,
                content[:50],
            )
        except Exception as e:
            logger.error("QQ 处理频道消息异常: %s", e)

    async def handle_group_message(self, message: GroupMessage) -> None:
        """处理群聊 @消息."""
        try:
            author_id = str(message.author.member_openid)
            if not self.is_allowed(author_id):
                logger.warning("QQ 群聊消息来自未授权用户: %s", author_id)
                return

            content = self.strip_at_mention(message.content or "", [])

            session_key = f"group_{message.group_openid}"
            session_id = self._sessions.get(session_key)
            if not session_id:
                session_id = f"qq_group_{message.group_openid}"
                self._sessions[session_key] = session_id

            user_msg = Message(
                id=message.msg_id,
                type="req",
                channel_id=self.channel_id,
                session_id=session_id,
                params={"content": content, "query": content},
                timestamp=time.time(),
                ok=True,
                req_method=ReqMethod.CHAT_SEND,
                metadata={
                    "qq_scene": "group",
                    "group_openid": message.group_openid,
                    "user_id": author_id,
                    "message_id": message.msg_id,
                },
            )

            await self._dispatch(user_msg)

            logger.info(
                "QQ 群聊消息: group=%s user=%s text=%s",
                message.group_openid, author_id, content[:50],
            )
        except Exception as e:
            logger.error("QQ 处理群聊消息异常: %s", e)

    async def handle_c2c_message(self, message: Any) -> None:
        """处理单聊私信消息."""
        try:
            author_id = str(getattr(message, "author", None))
            if not author_id:
                author_id = str(getattr(message, "user_openid", ""))
            if not self.is_allowed(author_id):
                logger.warning("QQ 单聊消息来自未授权用户: %s", author_id)
                return

            content = getattr(message, "content", "") or ""

            session_key = f"c2c_{author_id}"
            session_id = self._sessions.get(session_key)
            if not session_id:
                session_id = f"qq_c2c_{author_id}"
                self._sessions[session_key] = session_id

            user_msg = Message(
                id=str(getattr(message, "id", "")),
                type="req",
                channel_id=self.channel_id,
                session_id=session_id,
                params={"content": content, "query": content},
                timestamp=time.time(),
                ok=True,
                req_method=ReqMethod.CHAT_SEND,
                metadata={
                    "qq_scene": "c2c",
                    "user_openid": author_id,
                    "message_id": str(getattr(message, "id", "")),
                },
            )

            await self._dispatch(user_msg)

            logger.info(
                "QQ 单聊消息: user=%s text=%s",
                author_id, content[:50],
            )
        except Exception as e:
            logger.error("QQ 处理单聊消息异常: %s", e)

    async def _dispatch(self, msg: Message) -> None:
        """分发消息到 Gateway 或 Router."""
        if self._on_message_cb:
            result = self._on_message_cb(msg)
            if asyncio.iscoroutine(result):
                await result
        else:
            await self.bus.route_user_message(msg)

    @staticmethod
    def strip_at_mention(content: str, mentions: Any = None) -> str:
        """去掉消息中的 @机器人 提及部分."""
        text = content.strip()
        # botpy mentions 是 User 对象列表，每个有 id 字段
        if mentions and isinstance(mentions, (list, tuple)):
            for m in mentions:
                uid = getattr(m, "id", None)
                if uid:
                    text = text.replace(f"<@{uid}>", "").strip()
        # 兜底：去掉开头的 @
        if text.startswith("@"):
            text = text[1:].strip()
        return text

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
            source="qq",
            extra={
                "app_id": self.config.app_id,
                "enable_guild": self.config.enable_guild,
                "enable_group": self.config.enable_group,
                "enable_c2c": self.config.enable_c2c,
            },
        )
