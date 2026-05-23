# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""身份信息扩展模块。

提供身份信息扩展机制，允许业务层在 AgentServer 与 Gateway 建立 WebSocket
连接时获取 User ID、Domain ID、Application ID，并自动将这些信息附加到每条日志中。

Usage:
    # 业务层实现
    class MyIdentityProvider(IdentityProviderBase):
        async def fetch_identity(self) -> IdentityInfo:
            return IdentityInfo(user_id="user-123", domain_id="domain-abc")

    # 注册
    IdentityStore.get_instance().register_provider(MyIdentityProvider())
"""

from __future__ import annotations

from jiuwenclaw.extensions.identity_provider.types import IdentityInfo
from jiuwenclaw.extensions.identity_provider.base import IdentityProviderBase
from jiuwenclaw.extensions.identity_provider.store import IdentityStore

__all__ = [
    "IdentityInfo",
    "IdentityProviderBase",
    "IdentityStore",
]