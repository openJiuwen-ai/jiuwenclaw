# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway HTTP API surface (avatar chat and related REST endpoints)."""

from jiuwenavatar.gateway.http_api.avatar_chat import (
    AvatarChatService,
    build_avatar_http_app,
)

__all__ = [
    "AvatarChatService",
    "build_avatar_http_app",
]
