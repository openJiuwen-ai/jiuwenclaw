# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Web 消息规范化与转发逻辑."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jiuwenclaw.gateway.channel_manager import ChannelManager

from jiuwenclaw.schema.message import Message, ReqMethod

logger = logging.getLogger(__name__)

_FORWARD_REQ_METHODS: set[str] = {
    "chat.send",
    "chat.interrupt",
    "chat.resume",
    "chat.user_answer",
    "history.get",
    "browser.start",
    "skills.marketplace.list",
    "skills.list",
    "skills.installed",
    "skills.get",
    "skills.install",
    "skills.import_local",
    "skills.marketplace.add",
    "skills.marketplace.remove",
    "skills.marketplace.toggle",
    "skills.uninstall",
    "skills.skillnet.search",
    "skills.skillnet.install",
    "skills.skillnet.install_status",
    "skills.skillnet.evaluate",
    "skills.clawhub.get_token",
    "skills.clawhub.set_token",
    "skills.clawhub.search",
    "skills.clawhub.download",
    "skills.evolution.status",
    "skills.evolution.get",
    "skills.evolution.save",
}

_FORWARD_NO_LOCAL_HANDLER_METHODS: set[str] = {
    "browser.start",
    "skills.marketplace.list",
    "skills.list",
    "skills.installed",
    "skills.get",
    "skills.install",
    "skills.import_local",
    "skills.marketplace.add",
    "skills.marketplace.remove",
    "skills.marketplace.toggle",
    "skills.uninstall",
    "skills.skillnet.search",
    "skills.skillnet.install",
    "skills.skillnet.install_status",
    "skills.skillnet.evaluate",
    "skills.clawhub.get_token",
    "skills.clawhub.set_token",
    "skills.clawhub.search",
    "skills.clawhub.download",
    "skills.evolution.status",
    "skills.evolution.get",
    "skills.evolution.save",
}

FORWARD_REQ_METHODS = frozenset(_FORWARD_REQ_METHODS)
FORWARD_NO_LOCAL_HANDLER_METHODS = frozenset(_FORWARD_NO_LOCAL_HANDLER_METHODS)


def register_forward_method(method: str, skip_local: bool = False) -> None:
    """注册新的转发方法.

    Args:
        method: 方法名称，如 "custom.action"
        skip_local: 是否跳过本地处理（仅转发），默认 False

    Note:
        注册的方法会动态添加到转发列表中，但不会影响 FORWARD_REQ_METHODS 常量。
        如需获取最新的转发方法列表，请使用 get_forward_methods()。
    """
    _FORWARD_REQ_METHODS.add(method)
    if skip_local:
        _FORWARD_NO_LOCAL_HANDLER_METHODS.add(method)
    logger.info("[WebNormalize] 已注册转发方法: %s (skip_local=%s)", method, skip_local)


def unregister_forward_method(method: str) -> None:
    """取消注册转发方法.

    Args:
        method: 方法名称
    """
    _FORWARD_REQ_METHODS.discard(method)
    _FORWARD_NO_LOCAL_HANDLER_METHODS.discard(method)
    logger.info("[WebNormalize] 已取消注册转发方法: %s", method)


def get_forward_methods() -> set[str]:
    """获取当前所有转发方法.

    Returns:
        转发方法集合（可直接修改）
    """
    return _FORWARD_REQ_METHODS


def get_skip_local_methods() -> set[str]:
    """获取当前所有跳过本地处理的方法.

    Returns:
        跳过本地处理的方法集合（可直接修改）
    """
    return _FORWARD_NO_LOCAL_HANDLER_METHODS


def normalize_web_message(msg: Message) -> Message:
    """规范化 WebChannel 消息.

    Args:
        msg: 原始 WebChannel 消息

    Returns:
        规范化后的消息
    """
    method_val = getattr(getattr(msg, "req_method", None), "value", None) or ""

    is_stream = bool(
        msg.is_stream
        or method_val in (ReqMethod.CHAT_SEND.value, ReqMethod.HISTORY_GET.value)
    )

    params = dict(msg.params or {})
    if "query" not in params and "content" in params:
        params["query"] = params["content"]

    return Message(
        id=msg.id,
        type=msg.type,
        channel_id=msg.channel_id,
        session_id=msg.session_id,
        params=params,
        timestamp=msg.timestamp,
        ok=msg.ok,
        req_method=getattr(msg, "req_method", None) or ReqMethod.CHAT_SEND,
        mode=msg.mode,
        is_stream=is_stream,
        stream_seq=msg.stream_seq,
        stream_id=msg.stream_id,
        metadata=msg.metadata,
    )


def should_forward_message(msg: Message) -> bool:
    """判断消息是否需要转发到 MessageHandler.

    Args:
        msg: WebChannel 消息

    Returns:
        是否需要转发
    """
    method_val = getattr(getattr(msg, "req_method", None), "value", None) or ""
    return method_val in _FORWARD_REQ_METHODS


def should_skip_local_handler(msg: Message) -> bool:
    """判断消息是否跳过本地处理（仅转发）.

    Args:
        msg: WebChannel 消息

    Returns:
        是否跳过本地处理
    """
    method_val = getattr(getattr(msg, "req_method", None), "value", None) or ""
    return method_val in _FORWARD_NO_LOCAL_HANDLER_METHODS


def create_web_forward_handler(channel_manager: ChannelManager):
    """创建 WebChannel 消息转发处理器.

    Args:
        channel_manager: ChannelManager 实例

    Returns:
        消息处理回调函数，返回 True 表示跳过本地处理
    """

    def _norm_and_forward(msg: Message) -> bool:
        if not should_forward_message(msg):
            return False

        normalized = normalize_web_message(msg)
        channel_manager.deliver_to_message_handler(normalized)
        logger.info(
            "[App] Web 入站 -> MessageHandler: id=%s channel_id=%s",
            msg.id,
            msg.channel_id,
        )

        return should_skip_local_handler(msg)

    return _norm_and_forward


__all__ = [
    "FORWARD_REQ_METHODS",
    "FORWARD_NO_LOCAL_HANDLER_METHODS",
    "register_forward_method",
    "unregister_forward_method",
    "get_forward_methods",
    "get_skip_local_methods",
    "normalize_web_message",
    "should_forward_message",
    "should_skip_local_handler",
    "create_web_forward_handler",
]
