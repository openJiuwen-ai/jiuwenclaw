# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""身份信息全局存储（单例模式）。"""

from __future__ import annotations

import logging

from jiuwenclaw.extensions.identity_provider.types import IdentityInfo
from jiuwenclaw.extensions.identity_provider.base import IdentityProviderBase


logger = logging.getLogger(__name__)


class IdentityStore:
    """身份信息全局存储（单例模式）。

    存储 AgentServer 获取的身份信息，供日志系统读取。
    在 AgentWebSocketServer._connection_handler() 中触发身份获取。

    Example:
        >>> store = IdentityStore.get_instance()
        >>> store.register_provider(MyIdentityProvider())
        >>> await store.fetch_and_store()
        >>> identity = store.get_identity()
    """

    _instance: IdentityStore | None = None

    def __init__(self) -> None:
        self._identity: IdentityInfo | None = None
        self._provider: IdentityProviderBase | None = None
        self._fetched: bool = False

    @classmethod
    def get_instance(cls) -> IdentityStore:
        """获取单例实例。

        Returns:
            IdentityStore: 单例实例。
        """
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（用于测试）。

        清除单例实例和所有状态，用于测试隔离。
        """
        cls._instance = None

    def register_provider(self, provider: IdentityProviderBase) -> None:
        """注册身份提供者。

        业务层在启动时调用此方法注册实现类。

        Args:
            provider: IdentityProviderBase 的业务层实现实例。
        """
        self._provider = provider
        logger.info("[IdentityStore] 已注册身份提供者: %s", type(provider).__name__)

    def unregister_provider(self) -> None:
        """注销身份提供者。

        清除已注册的 provider 和存储的身份信息。
        """
        self._provider = None
        self._identity = None
        self._fetched = False

    def get_identity(self) -> IdentityInfo | None:
        """获取当前身份信息（供日志系统读取）。

        Returns:
            IdentityInfo | None: 当前存储的身份信息，未获取时返回 None。
        """
        return self._identity

    def get_provider(self) -> IdentityProviderBase | None:
        """获取已注册的身份提供者。

        Returns:
            IdentityProviderBase | None: 已注册的身份提供者，未注册时返回 None。
        """
        return self._provider

    def is_fetched(self) -> bool:
        """是否已获取身份信息。

        Returns:
            bool: True 表示已获取（无论成功或失败），False 表示未获取。
        """
        return self._fetched

    def set_test_state(
        self,
        identity: IdentityInfo | None = None,
        fetched: bool = True,
    ) -> None:
        """设置测试状态（仅用于单元测试）。

        Args:
            identity: 身份信息，可为 None。
            fetched: 是否已获取标记。
        """
        self._identity = identity
        self._fetched = fetched

    async def fetch_and_store(self) -> IdentityInfo | None:
        """调用 provider 获取身份并存储。

        在 AgentWebSocketServer._connection_handler() 中调用。
        获取失败时允许连接继续，日志字段为 null。

        Returns:
            IdentityInfo | None: 获取的身份信息，失败时返回 None 或 fallback。
        """
        if self._provider is None:
            logger.debug("[IdentityStore] 未注册身份提供者，跳过获取")
            self._fetched = True
            return None

        try:
            identity = await self._provider.fetch_identity()
            self._identity = identity
            self._fetched = True
            logger.info(
                "[IdentityStore] 身份信息已获取: user_id=%s domain_id=%s app_id=%s",
                identity.user_id,
                identity.domain_id,
                identity.app_id,
            )
            return identity
        except Exception as e:
            logger.warning("[IdentityStore] 身份获取失败: %s", e)
            self._fetched = True
            # 调用失败回调
            fallback = await self._provider.on_fetch_failed(e)
            if fallback is not None:
                self._identity = fallback
                logger.info(
                    "[IdentityStore] 使用 fallback 身份: user_id=%s domain_id=%s app_id=%s",
                    fallback.user_id,
                    fallback.domain_id,
                    fallback.app_id,
                )
            return self._identity