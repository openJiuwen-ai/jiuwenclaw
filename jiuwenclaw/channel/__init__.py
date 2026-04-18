# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Channel 模块 - 客户端连接抽象."""

from jiuwenclaw.channel.base import BaseChannel, ChannelMetadata
from jiuwenclaw.channel.web_channel import WebChannel
from jiuwenclaw.channel.xiaoyi_channel import XiaoyiChannel, XiaoyiChannelConfig
from jiuwenclaw.channel.telegram_channel import TelegramChannel, TelegramChannelConfig
from jiuwenclaw.channel.discord_channel import DiscordChannel, DiscordChannelConfig
from jiuwenclaw.channel.dingding import DingTalkChannel, DingTalkConfig
from jiuwenclaw.channel.whatsapp_channel import WhatsAppChannel, WhatsAppChannelConfig
from jiuwenclaw.channel.wecom_channel import WecomChannel, WecomConfig
from jiuwenclaw.channel.wechat_channel import WechatChannel, WechatConfig
from jiuwenclaw.channel.qq_channel import QQChannel, QQChannelConfig
from jiuwenclaw.channel.weibo_channel import WeiboChannel, WeiboChannelConfig

__all__ = [
    "BaseChannel",
    "ChannelMetadata",
    "WebChannel",
    "XiaoyiChannel",
    "XiaoyiChannelConfig",
    "TelegramChannel",
    "TelegramChannelConfig",
    "DiscordChannel",
    "DiscordChannelConfig",
    "DingTalkChannel",
    "DingTalkConfig",
    "WhatsAppChannel",
    "WhatsAppChannelConfig",
    "WecomChannel",
    "WecomConfig",
    "WechatChannel",
    "WechatConfig",
    "QQChannel",
    "QQChannelConfig",
    "WeiboChannel",
    "WeiboChannelConfig",
]
