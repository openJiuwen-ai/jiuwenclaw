# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Channel 模块 - 客户端连接抽象."""

from jiuwenclaw.channel.base import BaseChannel, ChannelMetadata
from jiuwenclaw.channel.web_channel import WebChannel
from jiuwenclaw.channel.xiaoyi_channel import XiaoyiChannel, XiaoyiChannelConfig
from jiuwenclaw.channel.dingding import DingTalkChannel, DingTalkConfig


__all__ = [
    "BaseChannel",
    "ChannelMetadata",
    "WebChannel",
    "XiaoyiChannel",
    "XiaoyiChannelConfig",
    "DingTalkChannel",
    "DingTalkConfig",
]
