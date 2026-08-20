# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Channel 模块 - 客户端连接抽象（惰性加载，避免启动时拉入重型 SDK）。"""

import sys

from jiuwenclaw._lazy import install_lazy_attrs

_LAZY_ATTRS = {
    "BaseChannel": (".base", "BaseChannel"),
    "ChannelMetadata": (".base", "ChannelMetadata"),
    "WebChannel": (".web_channel", "WebChannel"),
    "XiaoyiChannel": (".xiaoyi_channel", "XiaoyiChannel"),
    "XiaoyiChannelConfig": (".xiaoyi_channel", "XiaoyiChannelConfig"),
    "TelegramChannel": (".telegram_channel", "TelegramChannel"),
    "TelegramChannelConfig": (".telegram_channel", "TelegramChannelConfig"),
    "DiscordChannel": (".discord_channel", "DiscordChannel"),
    "DiscordChannelConfig": (".discord_channel", "DiscordChannelConfig"),
    "DingTalkChannel": (".dingding", "DingTalkChannel"),
    "DingTalkConfig": (".dingding", "DingTalkConfig"),
    "WhatsAppChannel": (".whatsapp_channel", "WhatsAppChannel"),
    "WhatsAppChannelConfig": (".whatsapp_channel", "WhatsAppChannelConfig"),
    "WecomChannel": (".wecom_channel", "WecomChannel"),
    "WecomConfig": (".wecom_channel", "WecomConfig"),
    "WechatChannel": (".wechat_channel", "WechatChannel"),
    "WechatConfig": (".wechat_channel", "WechatConfig"),
    "AcpChannel": (".acp_channel", "AcpChannel"),
    "AcpChannelConfig": (".acp_channel", "AcpChannelConfig"),
}

install_lazy_attrs(sys.modules[__name__], _LAZY_ATTRS)
