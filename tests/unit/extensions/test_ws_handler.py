# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""WebSocket 扩展处理器注册单元测试."""

from unittest.mock import MagicMock

import pytest

from jiuwenclaw.extensions.registry import ExtensionRegistry


@pytest.fixture
def mock_callback_framework():
    """Mock AsyncCallbackFramework."""
    return MagicMock()


@pytest.fixture
def mock_logger():
    """Mock logger."""
    return MagicMock()


@pytest.fixture
def registry(mock_callback_framework, mock_logger):
    """创建 ExtensionRegistry 实例."""
    # 重置单例
    ExtensionRegistry.reset_instance()
    return ExtensionRegistry.create_instance(
        callback_framework=mock_callback_framework,
        config={},
        logger=mock_logger,
    )


class TestRegisterWsHandlerValidation:
    """register_ws_handler 入参验证测试."""

    @staticmethod
    def test_register_with_empty_method_raises_value_error(registry):
        """空 method 应抛出 ValueError."""

        async def dummy_handler() -> dict:
            return {}

        with pytest.raises(ValueError, match="method must be a non-empty string"):
            registry.register_ws_handler(method="", handler=dummy_handler)

    @staticmethod
    def test_register_with_none_method_raises_value_error(registry):
        """None method 应抛出 ValueError."""

        async def dummy_handler() -> dict:
            return {}

        with pytest.raises(ValueError, match="method must be a non-empty string"):
            registry.register_ws_handler(method=None, handler=dummy_handler)

    @staticmethod
    def test_register_with_builtin_req_method_raises_runtime_error(registry):
        """与内置 ReqMethod 冲突应抛出 RuntimeError."""

        async def dummy_handler() -> dict:
            return {}

        # chat.send 是内置 ReqMethod
        with pytest.raises(RuntimeError, match="conflicts with built-in ReqMethod"):
            registry.register_ws_handler(method="chat.send", handler=dummy_handler)

    @staticmethod
    def test_register_duplicate_method_raises_runtime_error(registry):
        """重复注册同一 method 应抛出 RuntimeError."""

        async def handler1() -> dict:
            return {"v": 1}

        async def handler2() -> dict:
            return {"v": 2}

        registry.register_ws_handler(method="myapp.action1", handler=handler1)

        with pytest.raises(RuntimeError, match="already registered"):
            registry.register_ws_handler(method="myapp.action1", handler=handler2)


class TestRegisterWsHandlerSuccess:
    """register_ws_handler 正常注册测试."""

    @staticmethod
    def test_register_success(registry):
        """正常注册应成功。"""

        async def my_handler() -> dict:
            return {"result": "ok"}

        registry.register_ws_handler(method="myapp.custom_action", handler=my_handler)

        # 验证可以查找到
        entry = registry.get_ws_handler("myapp.custom_action")
        assert entry is not None
        assert entry.method == "myapp.custom_action"
        assert entry.handler == my_handler

    @staticmethod
    def test_get_ws_handler_returns_none_for_unregistered(registry):
        """未注册的 method 应返回 None。"""
        entry = registry.get_ws_handler("nonexistent.method")
        assert entry is None

    @staticmethod
    def test_list_ws_handlers_returns_all(registry):
        """list_ws_handlers 应返回所有已注册处理器。"""

        async def handler1() -> dict:
            return {"v": 1}

        async def handler2() -> dict:
            return {"v": 2}

        registry.register_ws_handler(method="app1.action1", handler=handler1)
        registry.register_ws_handler(method="app2.action2", handler=handler2)

        handlers = registry.list_ws_handlers()
        assert len(handlers) == 2
        methods = [h.method for h in handlers]
        assert "app1.action1" in methods
        assert "app2.action2" in methods
