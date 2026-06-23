# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager WebSocket 客户端与协议。"""

from .manager_ws_client import ManagerWsClient, ManagerWsConfigPushHandler

__all__ = [
    "ManagerWsClient",
    "ManagerWsConfigPushHandler",
]
