# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""ChannelManager - Channel 生命周期管理抽象与实现."""

from __future__ import annotations

import asyncio
from abc import ABC
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from jiuwenclaw.channel.base import BaseChannel
    from jiuwenclaw.gateway.message_handler import MessageHandler
    from jiuwenclaw.schema.message import Message

from jiuwenclaw.utils import logger


class ChannelManager(ABC):
    """
    负责：
    1. Channel 的注册、注销与查找
    2. 将各 Channel 收到的消息/事件统一通过 MessageHandler.handle_message 转发
    3. 运行出队派发循环：从 MessageHandler 取出 AgentServer 响应并投递到对应 Channel
    """

    def __init__(
        self,
        message_handler: "MessageHandler",
        config: dict[str, Any] | None = None,
        on_config_updated: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._message_handler = message_handler
        self._channels: dict[str, "BaseChannel"] = {}
        self._dispatch_task: asyncio.Task | None = None
        self._running = False
        # 统一管理 Channel 相关配置（例如 FeishuChannel / XiaoyiChannel 等）。
        # 默认仅在网关侧使用；其他简单用法可以忽略该字段。
        self._config: dict[str, Any] = dict(config or {})
        self._on_config_updated = on_config_updated

    def _on_channel_message(self, msg: "Message") -> None:
        """Channel 同步 on_message 回调：交给 MessageHandler 处理（入队并最终发往 AgentServer）."""
        logger.info(
            "[ChannelManager] Channel 消息 -> MessageHandler: id=%s channel_id=%s",
            msg.id, msg.channel_id,
        )
        if not self._channels.get(msg.channel_id, None):
            logger.info(f"[ChannelManager] Channel: {msg.channel_id} closed, cancel this user message.")
            return

        self._message_handler.handle_message(msg)

    def register_channel(self, channel: "BaseChannel") -> None:
        """注册 Channel，并为其注册「收到消息时转发给 MessageHandler」的回调."""
        cid = channel.channel_id
        self._channels[cid] = channel
        channel.on_message(self._on_channel_message)
        logger.info("[ChannelManager] 已注册 Channel: channel_id=%s, 当前共 %d 个", cid, len(self._channels))

    def unregister_channel(self, channel_id: str) -> None:
        """注销指定 Channel."""
        self._channels.pop(channel_id, None)
        logger.info("[ChannelManager] 已注销 Channel: channel_id=%s", channel_id)

    def get_channel(self, channel_id: str) -> "BaseChannel | None":
        """根据 channel_id 获取 Channel."""
        return self._channels.get(channel_id)

    @property
    def enabled_channels(self) -> list[str]:
        """当前已注册的 Channel 标识列表."""
        return list(self._channels.keys())

    # ----- 配置管理接口 -----

    def get_conf(self, channel_id: str) -> dict[str, Any]:
        """返回指定 channel_id 的配置浅拷贝；不存在则返回空 dict."""
        conf = self._config.get(channel_id)
        return dict(conf) if isinstance(conf, dict) else {}

    async def set_conf(self, channel_id: str, new_conf: dict[str, Any]) -> None:
        """更新指定 channel_id 的配置，并在必要时触发重新实例化回调.

        内部仍维护完整的 Channel 配置字典，并将其整体传给 on_config_updated，
        以兼容现有回调实现（如根据 channels.feishu 重建 FeishuChannel）。
        """
        merged = dict(self._config)
        merged[channel_id] = dict(new_conf or {})
        self._config = merged
        cb = self._on_config_updated
        if cb is not None:
            await cb(self._config)

    async def set_config(self, new_conf: dict[str, Any]) -> None:
        """兼容保留：整体替换配置的旧接口（不推荐新调用方使用）."""
        self._config = dict(new_conf or {})
        cb = self._on_config_updated
        if cb is not None:
            await cb(self._config)

    def set_config_callback(
        self,
        callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """设置在配置更新时触发的回调，用于由外部实现具体的 Channel 重新实例化逻辑."""
        self._on_config_updated = callback

    async def _dispatch_robot_messages(self) -> None:
        """出队派发循环：从 MessageHandler 消费 robot_messages，按 channel_id 投递到对应 Channel."""
        # 仅当 MessageHandler 提供 consume_robot_messages 时才能派发
        consume = getattr(self._message_handler, "consume_robot_messages", None)
        if not callable(consume):
            logger.warning("MessageHandler has no consume_robot_messages, robot_messages dispatch skipped")
            return
        while self._running:
            try:
                msg = await consume(timeout=1.0)
                if msg is None:
                    continue
                logger.info(
                    "[ChannelManager] 从 robot_messages 取出，准备派发: id=%s channel_id=%s type=%s",
                    msg.id, msg.channel_id, msg.type,
                )
                channel = self._channels.get(msg.channel_id)
                if channel:
                    try:
                        await channel.send(msg)
                        logger.info(
                            "[ChannelManager] 已派发到 Channel: channel_id=%s id=%s",
                            msg.channel_id, msg.id,
                        )
                    except Exception as e:
                        logger.error("send to channel %s: %s", msg.channel_id, e, exc_info=True)
                else:
                    logger.warning(
                        "[ChannelManager] 未找到 Channel，丢弃 robot_messages: channel_id=%s id=%s",
                        msg.channel_id, msg.id,
                    )
            except asyncio.CancelledError:
                break

    async def start_dispatch(self) -> None:
        """启动出队派发任务（消费 MessageHandler.robot_messages 并发送到各 Channel）."""
        if self._dispatch_task is not None:
            return
        self._running = True
        self._dispatch_task = asyncio.create_task(self._dispatch_robot_messages())
        logger.info("[ChannelManager] 出队派发循环已启动 (robot_messages -> Channel.send)")

    async def stop_dispatch(self) -> None:
        """停止出队派发任务."""
        self._running = False
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
            self._dispatch_task = None
        logger.info("[ChannelManager] 出队派发循环已停止")
