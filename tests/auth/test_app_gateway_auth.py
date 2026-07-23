"""测试 app_gateway.py 中的 _init_auth_handler 和 get_auth_handler"""
import pytest
from unittest.mock import MagicMock
import sys

# mock 掉循环导入问题
import jiuwenswarm.gateway
jiuwenswarm.gateway.AgentServerClient = MagicMock()

# 初始化 ExtensionRegistry
from jiuwenswarm.extensions.registry import ExtensionRegistry
from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework
ExtensionRegistry.create_instance(
    callback_framework=MagicMock(spec=AsyncCallbackFramework),
    config={},
    logger=MagicMock(),
)

# 确保使用 PassthroughAuthenticator（防止 gateway.yaml 覆盖为 AgentOSAuthenticator）
from jiuwenswarm.gateway.auth.passthrough_authenticator import PassthroughAuthenticator
registry = ExtensionRegistry.get_instance()
registry.register_authenticator(PassthroughAuthenticator())

from jiuwenswarm.gateway.app_gateway import (
    _init_auth_handler,
    get_auth_handler,
)
from jiuwenswarm.gateway.auth.credential_authenticator import (
    TokenAuthenticator, AuthContext,
)

#PASS
class TestInitAuthHandler:

    def test_returns_authenticator_from_registry(self):
        """验证 _init_auth_handler 返回 ExtensionRegistry 中的认证器"""
        registry = ExtensionRegistry.get_instance()

        mock_auth = MagicMock(spec=TokenAuthenticator)
        registry.register_authenticator(mock_auth)

        result = _init_auth_handler()
        assert result is mock_auth

    def test_raises_if_registry_not_initialized(self):
        """验证 ExtensionRegistry 未初始化时抛出异常"""
        ExtensionRegistry.reset_instance()

        with pytest.raises(RuntimeError, match="ExtensionRegistry 尚未初始化"):
            _init_auth_handler()

        # 恢复 registry
        ExtensionRegistry.create_instance(
            callback_framework=MagicMock(spec=AsyncCallbackFramework),
            config={},
            logger=MagicMock(),
        )

#PASS
class TestGetAuthHandler:

    def test_returns_authenticator(self):
        """验证 get_auth_handler 返回认证器实例"""
        result = get_auth_handler()
        assert isinstance(result, TokenAuthenticator)

    def test_caches_instance(self):
        """验证多次调用返回同一对象（缓存机制）"""
        handler1 = get_auth_handler()
        handler2 = get_auth_handler()
        assert handler1 is handler2

    def test_authenticator_works(self):
        """验证返回的认证器可以正常认证"""
        handler = get_auth_handler()
        context = AuthContext(
            channel_type="web", credentials={}, headers={}, remote_addr="127.0.0.1",
        )
        import asyncio
        result = asyncio.run(handler.authenticate(context))
        assert result.success is True
        assert result.user_id == "anonymous"