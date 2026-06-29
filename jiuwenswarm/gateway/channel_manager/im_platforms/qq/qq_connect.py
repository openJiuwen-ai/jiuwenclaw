"""QQ official bot channel backed by qq-botpy."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import botpy
from botpy.message import GroupMessage, Message as BotpyMessage
from botpy.types.message import MarkdownPayload

from jiuwenswarm.common.schema.message import EventType, Message, ReqMethod
from jiuwenswarm.gateway.channel_manager.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenswarm.gateway.channel_manager.im_platforms.message_text import (
    extract_human_text,
    get_outbound_artifacts,
    should_skip_intermediate_message,
)

logger = logging.getLogger(__name__)

@dataclass
class QQChannelConfig:
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    allow_from: list[str] = field(default_factory=list)
    enable_streaming: bool = True
    enable_guild: bool = True
    enable_group: bool = True
    enable_c2c: bool = True


class _QQBotpyClient(botpy.Client):
    def __init__(self, channel: "QQChannel", intents: Any) -> None:
        super().__init__(intents=intents)
        self._channel = channel

    async def on_ready(self) -> None:
        self._channel.set_connected(True)
        logger.info("QQChannel BotPy connection ready")

    async def on_at_message_create(self, message: BotpyMessage) -> None:
        if self._channel.config.enable_guild:
            await self._channel.handle_guild_message(message)

    async def on_group_at_message_create(self, message: GroupMessage) -> None:
        if self._channel.config.enable_group:
            await self._channel.handle_group_message(message)

    async def on_c2c_message_create(self, message: Any) -> None:
        if self._channel.config.enable_c2c:
            await self._channel.handle_c2c_message(message)

    async def on_direct_message_create(self, message: Any) -> None:
        if self._channel.config.enable_c2c:
            await self._channel.handle_direct_message(message)


class QQChannel(BaseChannel):
    """QQ Bot channel supporting guild mentions, group mentions, and C2C text."""

    name = "qq"

    def __init__(self, config: QQChannelConfig, router: RobotMessageRouter) -> None:
        super().__init__(config, router)
        self.config = config
        self._botpy_client: Any = None
        self._run_task: asyncio.Task[None] | None = None
        self._on_message_cb: Callable[[Message], Any] | None = None
        self._sessions: dict[str, str] = {}
        self._reply_seq_by_msg_id: dict[str, int] = {}
        self._intents: Any = None
        self._connected = False

    def set_connected(self, value: bool) -> None:
        self._connected = value

    def set_test_client(self, client: Any, running: bool = True) -> None:
        """Inject a mock client for unit testing outbound sends."""
        self._botpy_client = client
        self._running = running

    @property
    def channel_id(self) -> str:
        return self.name

    def on_message(self, callback: Callable[[Message], Any]) -> None:
        self._on_message_cb = callback

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        if not self.config.enabled or not self.config.app_id or not self.config.app_secret:
            logger.error("QQChannel needs enabled, app_id, and app_secret")
            return
        if self._running:
            return

        intents = botpy.Intents.none()
        if self.config.enable_guild:
            intents.public_guild_messages = True
        if self.config.enable_group or self.config.enable_c2c:
            intents.public_messages = True
        if self.config.enable_c2c:
            intents.direct_message = True

        self._running = True
        self._intents = intents
        self._run_task = asyncio.create_task(self._run_botpy(), name="qq-botpy")
        logger.info(
            "QQChannel started: guild=%s group=%s c2c=%s public_guild_messages=%s public_messages=%s app_id=%s",
            self.config.enable_guild,
            self.config.enable_group,
            self.config.enable_c2c,
            getattr(intents, "public_guild_messages", None),
            getattr(intents, "public_messages", None),
            self.config.app_id,
        )
        while self._running:
            await asyncio.sleep(1)

    async def _run_botpy(self) -> None:
        backoff = 1.0
        while self._running:
            self._connected = False
            client = _QQBotpyClient(self, intents=self._intents)
            self._botpy_client = client
            try:
                async with client:
                    await client.start(appid=self.config.app_id, secret=self.config.app_secret)
                backoff = 1.0
            except Exception as exc:
                logger.error("QQChannel connection failed: %s", exc, exc_info=True)
            finally:
                self._connected = False
            if self._running:
                logger.info("QQChannel reconnecting in %.1fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def stop(self) -> None:
        self._running = False
        self._connected = False
        client = self._botpy_client
        self._botpy_client = None
        if client is not None:
            try:
                result = client.close()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                logger.warning("QQChannel close failed: %s", exc)
        task = self._run_task
        self._run_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def send(self, msg: Message) -> None:
        if not self._botpy_client or not self._running:
            logger.warning("QQChannel send skipped: client not running")
            return
        if should_skip_intermediate_message(msg):
            return
        content = self._extract_content(msg)
        if not content:
            logger.warning("QQChannel send skipped: empty content, payload=%s params=%s", msg.payload, msg.params)
            return
        metadata = msg.metadata or {}
        scene = str(metadata.get("qq_scene") or "guild")
        reply_msg_id = str(metadata.get("message_id") or "") or None
        target = ""
        try:
            images = get_outbound_artifacts(msg, "image")
            image_path = str(images[0].get("path") or "") if images else None
            if scene == "group":
                target = str(metadata.get("group_openid") or "")
                if target:
                    logger.info("QQChannel sending group message: target=%s", target)
                    await self._post_group_text(target, content, reply_msg_id)
            elif scene == "c2c":
                target = str(metadata.get("user_openid") or "")
                if target:
                    logger.info("QQChannel sending c2c message: target=%s", target)
                    await self._post_c2c_text(target, content, reply_msg_id)
            elif scene == "direct":
                target = str(metadata.get("guild_id") or "")
                if target:
                    logger.info("QQChannel sending direct message: target=%s", target)
                    await self._post_guild_markdown(
                        "post_dms",
                        "guild_id",
                        target,
                        content,
                        reply_msg_id,
                        image_path,
                    )
            else:
                target = str(metadata.get("qq_channel_id") or metadata.get("channel_id") or "")
                if target:
                    logger.info("QQChannel sending guild message: target=%s", target)
                    await self._post_guild_markdown(
                        "post_message",
                        "channel_id",
                        target,
                        content,
                        reply_msg_id,
                        image_path,
                    )
            if not target:
                logger.warning("QQChannel send skipped: missing target for scene=%s metadata=%s", scene, metadata)
        except Exception as exc:
            logger.error("QQChannel send failed: scene=%s target=%s error=%s", scene, target, exc, exc_info=True)

    async def _post_group_text(self, group_openid: str, content: str, reply_msg_id: str | None) -> None:
        for attempt in range(2):
            try:
                await self._botpy_client.api.post_group_message(
                    group_openid=group_openid,
                    msg_type=2,
                    markdown=MarkdownPayload(content=content),
                    msg_id=reply_msg_id,
                    msg_seq=self._next_reply_seq(reply_msg_id),
                )
                return
            except Exception as exc:
                if attempt == 0 and self._is_duplicate_msg_seq_error(exc):
                    logger.info("QQChannel group msg_seq duplicated, retrying with next seq: %s", exc)
                    continue
                raise

    async def _post_c2c_text(self, openid: str, content: str, reply_msg_id: str | None) -> None:
        for attempt in range(2):
            try:
                await self._botpy_client.api.post_c2c_message(
                    openid=openid,
                    msg_type=2,
                    markdown=MarkdownPayload(content=content),
                    msg_id=reply_msg_id,
                    msg_seq=self._next_reply_seq(reply_msg_id),
                )
                return
            except Exception as exc:
                if attempt == 0 and self._is_duplicate_msg_seq_error(exc):
                    logger.info("QQChannel c2c msg_seq duplicated, retrying with next seq: %s", exc)
                    continue
                raise

    async def _post_guild_markdown(
        self,
        method_name: str,
        target_key: str,
        target: str,
        content: str,
        reply_msg_id: str | None,
        image_path: str | None,
    ) -> None:
        method = getattr(self._botpy_client.api, method_name)
        common = {target_key: target, "file_image": image_path, "msg_id": reply_msg_id}
        await method(markdown=MarkdownPayload(content=content), **common)

    def _next_reply_seq(self, reply_msg_id: str | None) -> int:
        if not reply_msg_id:
            return 1
        next_seq = self._reply_seq_by_msg_id.get(reply_msg_id, 0) + 1
        self._reply_seq_by_msg_id[reply_msg_id] = next_seq
        if len(self._reply_seq_by_msg_id) > 1024:
            for key in list(self._reply_seq_by_msg_id)[:256]:
                self._reply_seq_by_msg_id.pop(key, None)
        return next_seq

    @staticmethod
    def _is_duplicate_msg_seq_error(exc: Exception) -> bool:
        text = str(exc)
        return "消息被去重" in text or "40054005" in text

    async def handle_guild_message(self, message: Any) -> None:
        author = str(getattr(getattr(message, "author", None), "id", ""))
        content = self._strip_mention(str(getattr(message, "content", "") or ""), getattr(message, "mentions", None))
        await self._dispatch_inbound(
            message_id=str(getattr(message, "id", "")),
            author=author,
            content=content,
            session_key=f"guild:{getattr(message, 'guild_id', '')}:{getattr(message, 'channel_id', '')}",
            metadata={
                "qq_scene": "guild",
                "guild_id": str(getattr(message, "guild_id", "")),
                "qq_channel_id": str(getattr(message, "channel_id", "")),
                "user_id": author,
            },
        )

    async def handle_group_message(self, message: Any) -> None:
        author = str(getattr(getattr(message, "author", None), "member_openid", ""))
        await self._dispatch_inbound(
            message_id=str(getattr(message, "msg_id", "") or getattr(message, "id", "")),
            author=author,
            content=self._strip_mention(str(getattr(message, "content", "") or "")),
            session_key=f"group:{getattr(message, 'group_openid', '')}",
            metadata={
                "qq_scene": "group",
                "group_openid": str(getattr(message, "group_openid", "")),
                "user_id": author,
            },
        )

    async def handle_c2c_message(self, message: Any) -> None:
        author_obj = getattr(message, "author", None)
        author = str(
            getattr(author_obj, "user_openid", "")
            or getattr(message, "user_openid", "")
            or author_obj
            or ""
        )
        await self._dispatch_inbound(
            message_id=str(getattr(message, "id", "") or getattr(message, "msg_id", "")),
            author=author,
            content=str(getattr(message, "content", "") or ""),
            session_key=f"c2c:{author}",
            metadata={"qq_scene": "c2c", "user_openid": author, "user_id": author},
        )

    async def handle_direct_message(self, message: Any) -> None:
        author_obj = getattr(message, "author", None)
        author = str(getattr(author_obj, "id", "") or getattr(message, "author_id", "") or "")
        guild_id = str(getattr(message, "guild_id", "") or "")
        await self._dispatch_inbound(
            message_id=str(getattr(message, "id", "") or getattr(message, "msg_id", "")),
            author=author,
            content=str(getattr(message, "content", "") or ""),
            session_key=f"direct:{guild_id}:{author}",
            metadata={
                "qq_scene": "direct",
                "guild_id": guild_id,
                "qq_channel_id": str(getattr(message, "channel_id", "") or ""),
                "user_id": author,
            },
        )

    async def _dispatch_inbound(
        self,
        *,
        message_id: str,
        author: str,
        content: str,
        session_key: str,
        metadata: dict[str, Any],
    ) -> None:
        if not author or not content or not self.is_allowed(author):
            logger.info(
                "QQChannel inbound ignored: has_author=%s has_content=%s allowed=%s metadata=%s",
                bool(author),
                bool(content),
                self.is_allowed(author) if author else False,
                metadata,
            )
            return
        logger.info("QQChannel inbound message: session_key=%s message_id=%s", session_key, message_id)
        session_id = self._sessions.setdefault(session_key, f"qq_{session_key}")
        metadata["message_id"] = message_id
        msg = Message(
            id=message_id or f"qq_{int(time.time() * 1000)}",
            type="req",
            channel_id=self.channel_id,
            session_id=session_id,
            params={"content": content, "query": content},
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            is_stream=self.config.enable_streaming,
            metadata=metadata,
        )
        if self._on_message_cb is not None:
            result = self._on_message_cb(msg)
            if inspect.isawaitable(result):
                await result
        else:
            await self.bus.route_user_message(msg)

    @staticmethod
    def _strip_mention(content: str, mentions: Any = None) -> str:
        text = content.strip()
        for mention in mentions or []:
            identifier = getattr(mention, "id", "")
            if identifier:
                text = text.replace(f"<@{identifier}>", "")
        return text.lstrip("@").strip()

    @staticmethod
    def _extract_content(msg: Message) -> str:
        if msg.event_type == EventType.CHAT_ERROR:
            return extract_human_text(msg)
        return extract_human_text(msg)

    # Public alias for testing convenience
    extract_content = _extract_content

    def get_metadata(self) -> ChannelMetadata:
        return ChannelMetadata(channel_id=self.channel_id, source="qq", extra={"app_id": self.config.app_id})
