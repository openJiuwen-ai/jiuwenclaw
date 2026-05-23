# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""IdentityStore 单例存储测试。"""

import pytest

from jiuwenclaw.extensions.identity_provider.types import IdentityInfo
from jiuwenclaw.extensions.identity_provider.base import IdentityProviderBase
from jiuwenclaw.extensions.identity_provider.store import IdentityStore


class MockIdentityProvider(IdentityProviderBase):
    """Mock 身份提供者，用于测试。"""

    def __init__(self, identity: IdentityInfo | None = None, should_fail: bool = False) -> None:
        self._identity = identity
        self._should_fail = should_fail
        self.fetch_called = False

    async def fetch_identity(self) -> IdentityInfo:
        self.fetch_called = True
        if self._should_fail:
            raise RuntimeError("Mock fetch failed")
        if self._identity is None:
            return IdentityInfo()
        return self._identity

    async def on_fetch_failed(self, error: Exception) -> IdentityInfo | None:
        """测试失败回调。"""
        return IdentityInfo(user_id="fallback-user")


class TestIdentityStore:
    """测试 IdentityStore 单例。"""

    @staticmethod
    def setup_method() -> None:
        """每个测试前重置单例。"""
        IdentityStore.reset_instance()

    @staticmethod
    def teardown_method() -> None:
        """每个测试后清理单例。"""
        IdentityStore.reset_instance()

    @staticmethod
    def test_singleton_same_instance() -> None:
        """测试单例返回相同实例。"""
        store1 = IdentityStore.get_instance()
        store2 = IdentityStore.get_instance()
        assert store1 is store2

    @staticmethod
    def test_reset_instance_clears_singleton() -> None:
        """测试 reset_instance 清除单例。"""
        store1 = IdentityStore.get_instance()
        IdentityStore.reset_instance()
        store2 = IdentityStore.get_instance()
        assert store1 is not store2

    @staticmethod
    def test_register_provider() -> None:
        """测试注册 provider。"""
        store = IdentityStore.get_instance()
        provider = MockIdentityProvider()
        store.register_provider(provider)
        assert store.get_provider() is provider

    @staticmethod
    def test_unregister_provider() -> None:
        """测试注销 provider。"""
        store = IdentityStore.get_instance()
        provider = MockIdentityProvider()
        store.register_provider(provider)
        store.unregister_provider()
        assert store.get_provider() is None
        assert store.get_identity() is None
        assert store.is_fetched() is False

    @staticmethod
    def test_get_identity_returns_none_before_fetch() -> None:
        """测试未获取时 get_identity 返回 None。"""
        store = IdentityStore.get_instance()
        assert store.get_identity() is None

    @pytest.mark.asyncio
    async def test_fetch_and_store_success(self) -> None:
        """测试成功获取并存储身份。"""
        store = IdentityStore.get_instance()
        identity = IdentityInfo(user_id="user-123", domain_id="domain-abc")
        provider = MockIdentityProvider(identity=identity)
        store.register_provider(provider)

        result = await store.fetch_and_store()

        assert result is not None
        assert result.user_id == "user-123"
        assert store.get_identity().user_id == "user-123"
        assert store.is_fetched() is True
        assert provider.fetch_called is True

    @pytest.mark.asyncio
    async def test_fetch_and_store_no_provider(self) -> None:
        """测试无 provider 时返回 None。"""
        store = IdentityStore.get_instance()

        result = await store.fetch_and_store()

        assert result is None
        assert store.is_fetched() is True

    @pytest.mark.asyncio
    async def test_fetch_and_store_failure_continues(self) -> None:
        """测试获取失败时连接继续。"""
        store = IdentityStore.get_instance()
        provider = MockIdentityProvider(should_fail=True)
        store.register_provider(provider)

        result = await store.fetch_and_store()

        assert store.is_fetched() is True
        assert store.get_identity() is not None  # fallback identity
        assert store.get_identity().user_id == "fallback-user"

    @pytest.mark.asyncio
    async def test_fetch_and_store_failure_no_fallback(self) -> None:
        """测试获取失败且无 fallback 时返回 None。"""
        store = IdentityStore.get_instance()

        class NoFallbackProvider(IdentityProviderBase):
            async def fetch_identity(self) -> IdentityInfo:
                raise RuntimeError("Failed")

            # 不实现 on_fetch_failed，使用默认返回 None

        provider = NoFallbackProvider()
        store.register_provider(provider)

        result = await store.fetch_and_store()

        assert store.is_fetched() is True
        assert store.get_identity() is None

    @staticmethod
    def test_is_fetched_initial_false() -> None:
        """测试初始 is_fetched 为 False。"""
        store = IdentityStore.get_instance()
        assert store.is_fetched() is False

    @pytest.mark.asyncio
    async def test_is_fetched_after_fetch(self) -> None:
        """测试获取后 is_fetched 为 True。"""
        store = IdentityStore.get_instance()
        provider = MockIdentityProvider()
        store.register_provider(provider)

        await store.fetch_and_store()

        assert store.is_fetched() is True