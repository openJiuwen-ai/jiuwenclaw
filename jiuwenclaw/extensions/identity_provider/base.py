# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""身份信息提供者抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from jiuwenclaw.extensions.identity_provider.types import IdentityInfo


class IdentityProviderBase(ABC):
    """身份信息提供者抽象基类。

    业务层需继承此类并实现 fetch_identity() 方法。
    在 jiuwenclaw 启动前通过 IdentityStore.register_provider() 注册。

    Example:
        >>> class MyIdentityProvider(IdentityProviderBase):
        ...     async def fetch_identity(self) -> IdentityInfo:
        ...         # 业务层实现：调用 API、读取配置等
        ...         return IdentityInfo(user_id="user-123")
        ...
        >>> IdentityStore.get_instance().register_provider(MyIdentityProvider())
    """

    @abstractmethod
    async def fetch_identity(self) -> IdentityInfo:
        """获取身份信息。

        业务层实现此方法，返回 User ID、Domain ID、Application ID。
        实现方式由业务层决定：
        - 调用外部 API
        - 读取配置文件
        - 从环境变量获取
        - 查询数据库

        Returns:
            IdentityInfo: 身份信息对象，字段可为 None。
        """
        ...

    async def on_fetch_failed(self, error: Exception) -> IdentityInfo | None:
        """获取失败时的回调（可选实现）。

        默认返回 None（允许连接继续）。
        业务层可覆盖此方法实现自定义失败处理。

        Args:
            error: 获取失败时的异常。

        Returns:
            IdentityInfo | None: 返回 None 表示允许连接继续，
            返回 IdentityInfo 表示使用 fallback 身份信息。
        """
        return None