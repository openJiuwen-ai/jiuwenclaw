# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentWebSocketServer 连接触发集成测试。"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock
import pytest

from jiuwenclaw.extensions.identity_provider.types import IdentityInfo
from jiuwenclaw.extensions.identity_provider.store import IdentityStore


class TestAgentWebSocketServerConnectionTrigger:
    """测试 AgentWebSocketServer 连接触发身份获取。"""
    
    @staticmethod
    def setup_method() -> None:
        """每个测试前重置单例。"""
        IdentityStore.reset_instance()

    @staticmethod
    def teardown_method() -> None:
        """每个测试后清理单例。"""
        IdentityStore.reset_instance()

    @pytest.mark.asyncio
    async def test_fetch_and_store_called_on_connection(self) -> None:
        """测试连接建立时触发 fetch_and_store。"""
        from jiuwenclaw.extensions.identity_provider.base import IdentityProviderBase

        class TestProvider(IdentityProviderBase):
            def __init__(self):
                self.fetch_called = False

            async def fetch_identity(self) -> IdentityInfo:
                self.fetch_called = True
                return IdentityInfo(user_id="conn-user", domain_id="conn-domain")

        provider = TestProvider()
        store = IdentityStore.get_instance()
        store.register_provider(provider)

        # Simulate the connection trigger
        result = await store.fetch_and_store()

        assert provider.fetch_called is True
        assert result is not None
        assert result.user_id == "conn-user"
        assert store.get_identity().user_id == "conn-user"

    @pytest.mark.asyncio
    async def test_connection_continues_on_fetch_failure(self) -> None:
        """测试身份获取失败时连接继续。"""
        from jiuwenclaw.extensions.identity_provider.base import IdentityProviderBase

        class FailingProvider(IdentityProviderBase):
            async def fetch_identity(self) -> IdentityInfo:
                raise RuntimeError("Connection failed")

        provider = FailingProvider()
        store = IdentityStore.get_instance()
        store.register_provider(provider)

        # Simulate the connection trigger - should not raise
        result = await store.fetch_and_store()

        # Connection should continue (no exception)
        assert store.is_fetched() is True

    @pytest.mark.asyncio
    async def test_connection_continues_without_provider(self) -> None:
        """测试无 provider 时连接继续。"""
        store = IdentityStore.get_instance()

        # Simulate the connection trigger without provider
        result = await store.fetch_and_store()

        # Should return None but not raise
        assert result is None
        assert store.is_fetched() is True