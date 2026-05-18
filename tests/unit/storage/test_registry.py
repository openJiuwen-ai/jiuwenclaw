# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

# pylint: disable=protected-access
# pylint: disable=no-self-argument
# 测试代码访问私有成员和不需要实例访问的测试方法是合理的测试实践

"""测试 StorageBackendRegistry 注册表功能."""

from unittest.mock import MagicMock

import pytest

from jiuwenclaw.storage.backend import BaseStorageBackend
from jiuwenclaw.storage.registry import StorageBackendRegistry


class MockBackend(BaseStorageBackend):
    """Mock Backend for testing."""

    def _validate_config(self, config):
        return config

    def _create_client(self):
        return MagicMock()

    async def upload_file(self, local_path, user_id, chat_id, channel_type):
        return f"mock://uri/{local_path}"

    async def download_file(self, uri, local_path):
        pass

    async def delete_chat_files(self, user_id, chat_id, channel_type, older_than_hours=None):
        return 0

    async def delete_user_files(self, user_id, older_than_hours=24):
        return 0


class TestStorageBackendRegistry:
    """测试 Registry 核心功能."""
    
    @staticmethod
    def setup_method():
        """每个测试方法前重置 Registry."""
        StorageBackendRegistry.reset()

    @staticmethod
    def test_manual_register():
        """测试手动注册 Backend 类."""
        # 注册 MockBackend
        StorageBackendRegistry.register("mock", MockBackend)

        # 验证注册成功
        backend_class = StorageBackendRegistry.get("mock")
        assert backend_class is MockBackend

    @staticmethod
    def test_register_invalid_backend():
        """测试注册非 BaseStorageBackend 子类."""
        with pytest.raises(TypeError, match="BaseStorageBackend 的子类"):
            StorageBackendRegistry.register("invalid", str)

    @staticmethod
    def test_get_unregistered_backend():
        """测试查找未注册的 Backend."""
        backend_class = StorageBackendRegistry.get("nonexistent")
        assert backend_class is None

    @staticmethod
    def test_list_available_empty():
        """测试空注册表列表."""
        backends = StorageBackendRegistry.list_available()
        assert backends == []

    @staticmethod
    def test_list_available_with_backends():
        """测试有后端的注册表列表."""
        StorageBackendRegistry.register("mock1", MockBackend)
        StorageBackendRegistry.register("mock2", MockBackend)

        backends = StorageBackendRegistry.list_available()
        assert "mock1" in backends
        assert "mock2" in backends
        assert len(backends) == 2

    @staticmethod
    def test_register_from_config_with_backend_class():
        """测试配置驱动自动注册."""
        # 模拟配置中有 backend_class 字段
        config = {
            "type": "custom",
            "backend_class": "tests.unit.storage.test_registry.MockBackend"
        }

        # 执行配置驱动注册
        StorageBackendRegistry.register_from_config(config)

        # 验证注册成功
        backend_class = StorageBackendRegistry.get("custom")
        # 由于 Python 导入机制，使用类名比较而不是身份比较
        assert backend_class.__name__ == MockBackend.__name__
        assert issubclass(backend_class, BaseStorageBackend)

    @staticmethod
    def test_register_from_config_without_backend_class():
        """测试配置中没有 backend_class 字段."""
        config = {
            "type": "local",
            "root_path": "/tmp/storage"
        }

        # 执行配置驱动注册（应无操作）
        StorageBackendRegistry.register_from_config(config)

        # 验证没有新 Backend 注册
        backend_class = StorageBackendRegistry.get("local")
        # local 是 SDK 内置的，应该已经在 __init__.py 中注册
        # 这里只是确保没有异常抛出

    @staticmethod
    def test_register_from_config_invalid_module():
        """测试配置驱动注册失败（模块不存在）."""
        config = {
            "type": "invalid",
            "backend_class": "nonexistent.module.InvalidBackend"
        }

        # 执行配置驱动注册（应抛出 ImportError）
        with pytest.raises(ImportError, match="模块不存在"):
            StorageBackendRegistry.register_from_config(config)

        # 验证没有 Backend 注册
        backend_class = StorageBackendRegistry.get("invalid")
        assert backend_class is None

    @staticmethod
    def test_reset_clears_registry():
        """测试 reset() 清空注册表."""
        # 注册几个 Backend
        StorageBackendRegistry.register("mock1", MockBackend)
        StorageBackendRegistry.register("mock2", MockBackend)

        # 清空注册表
        StorageBackendRegistry.reset()

        # 验证注册表已清空
        backends = StorageBackendRegistry.list_available()
        assert backends == []

    @staticmethod
    def test_double_register_same_name():
        """测试重复注册同名 Backend（覆盖）."""
        StorageBackendRegistry.register("mock", MockBackend)

        # 创建另一个 Mock Backend
        class AnotherMockBackend(BaseStorageBackend):
            def _validate_config(self, config):
                return config

            def _create_client(self):
                return MagicMock()

            async def upload_file(self, local_path, user_id, chat_id, channel_type):
                return f"another://uri/{local_path}"

            async def download_file(self, uri, local_path):
                pass

            async def delete_chat_files(self, user_id, chat_id, channel_type, older_than_hours=None):
                return 0

            async def delete_user_files(self, user_id, older_than_hours=24):
                return 0

        # 重复注册同名 Backend（覆盖）
        StorageBackendRegistry.register("mock", AnotherMockBackend)

        # 验证已覆盖
        backend_class = StorageBackendRegistry.get("mock")
        assert backend_class is AnotherMockBackend