# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Storage 工具函数."""

import re
import logging

logger = logging.getLogger(__name__)


def sanitize_chat_id(chat_id: str, channel_type: str) -> str:
    """清理 chat_id 中的特殊字符，确保文件系统路径安全.

    Args:
        chat_id: 原始 chat_id
        channel_type: 渠道类型 (web, dingtalk, xiaoyi, wecom 等)

    Returns:
        清理后的 chat_id

    Raises:
        ValueError: 如果 chat_id 为空或清理后为空字符串
    """
    if not chat_id:
        raise ValueError("chat_id 不能为空")

    if not channel_type:
        raise ValueError("channel_type 不能为空")

    original_id = chat_id

    # 根据 channel 类型选择清理策略
    if channel_type in ["dingtalk", "wecom"]:
        # DingTalk 和 Wecom 的 chat_id 相对规范，移除特殊字符
        cleaned = re.sub(r'[^a-zA-Z0-9]', "", str(chat_id))
    else:
        # 通用清理：保留字母、数字、下划线、横线
        cleaned = re.sub(r'[^a-zA-Z0-9_-]', "", str(chat_id))

    if not cleaned:
        raise ValueError(f"chat_id 清理后为空字符串: original='{original_id}', channel='{channel_type}'")

    logger.debug("sanitize_chat_id: channel=%s, original='%s' -> cleaned='%s'", channel_type, original_id, cleaned)
    return cleaned


def build_chat_prefix(channel_type: str, chat_id: str) -> str:
    """构建 chat 路径前缀.

    Args:
        channel_type: 渠道类型
        chat_id: 会话 ID (假设已经过 sanitize_chat_id 清理)

    Returns:
        路径前缀，格式为 "{channel_type}_{chat_id}"

    Raises:
        ValueError: 如果参数为空
    """
    if not channel_type:
        raise ValueError("channel_type 不能为空")

    if not chat_id:
        raise ValueError("chat_id 不能为空")

    prefix = f"{channel_type}_{chat_id}"
    logger.debug("build_chat_prefix: %s", prefix)
    return prefix


def build_object_key(
    user_id: str,
    channel_type: str,
    chat_id: str,
    timestamp: str,
    filename: str
) -> str:
    """构建对象存储 Key.

    Args:
        user_id: 用户 ID
        channel_type: 渠道类型
        chat_id: 会话 ID (假设已经过 sanitize_chat_id 清理)
        timestamp: 时间戳 (格式: YYYYMMDD_HHMMSS)
        filename: 文件名

    Returns:
        对象存储 Key，格式为 "files/{user_id}/{channel_type}_{chat_id}/{timestamp}/{filename}"

    Raises:
        ValueError: 如果任何必需参数为空
    """
    if not user_id:
        raise ValueError("user_id 不能为空")

    if not channel_type:
        raise ValueError("channel_type 不能为空")

    if not chat_id:
        raise ValueError("chat_id 不能为空")

    if not timestamp:
        raise ValueError("timestamp 不能为空")

    if not filename:
        raise ValueError("filename 不能为空")

    chat_prefix = build_chat_prefix(channel_type, chat_id)
    object_key = f"files/{user_id}/{chat_prefix}/{timestamp}/{filename}"

    logger.debug("build_object_key: %s", object_key)
    return object_key