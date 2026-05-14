# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""测试重构后的存储后端抽象基类"""

from unittest.mock import MagicMock, patch
import pytest

from jiuwenclaw.storage.backend import BaseStorageBackend, StorageBackend


class TestBaseStorageBackend:
    """测试抽象基类"""

    def test_storage_backend_alias(self):
        """测试向后兼容：StorageBackend 是 BaseStorageBackend 的别名"""
        assert StorageBackend is BaseStorageBackend

    def test_base_initialization(self):
        """测试基类初始化"""

        class TestBackend(BaseStorageBackend):
            def _validate_config(self, config):
                super()._validate_config(config)
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

        backend = TestBackend({"bucket": "test"})

        # 验证基础属性设置
        assert backend.bucket == "test"
        assert backend.access_key == ""
        assert backend.secret_key == ""
        assert backend.endpoint == ""
        assert backend.region == ""

    def test_config_validation(self):
        """测试配置验证"""

        class TestBackend(BaseStorageBackend):
            def _validate_config(self, config):
                super()._validate_config(config)
                if not config.get("required_field"):
                    raise ValueError("缺少 required_field")
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

        # 测试验证失败
        with pytest.raises(ValueError, match="缺少 required_field"):
            TestBackend({"bucket": "test"})

    def test_lazy_client_creation(self):
        """测试懒加载客户端"""

        class TestBackend(BaseStorageBackend):
            def _validate_config(self, config):
                return config

            def _create_client(self):
                return MagicMock(client="test_client")

            async def upload_file(self, local_path, user_id, chat_id, channel_type):
                return f"mock://uri/{local_path}"

            async def download_file(self, uri, local_path):
                pass

            async def delete_chat_files(self, user_id, chat_id, channel_type, older_than_hours=None):
                return 0

            async def delete_user_files(self, user_id, older_than_hours=24):
                return 0

        backend = TestBackend({"bucket": "test"})

        # 验证懒加载：初始为 None
        assert backend._client is None

        # 首次调用 _get_client() 时创建
        client = backend._get_client()
        assert client is not None
        assert client.client == "test_client"

        # 后续调用返回同一实例
        client2 = backend._get_client()
        assert client is client2

    def test_connection_test(self):
        """测试连接测试"""

        class TestBackend(BaseStorageBackend):
            def _validate_config(self, config):
                return config

            def _create_client(self):
                return MagicMock()

            def _test_connection(self):
                if self._get_client() is None:
                    raise ValueError("客户端创建失败")

            async def upload_file(self, local_path, user_id, chat_id, channel_type):
                return f"mock://uri/{local_path}"

            async def download_file(self, uri, local_path):
                pass

            async def delete_chat_files(self, user_id, chat_id, channel_type, older_than_hours=None):
                return 0

            async def delete_user_files(self, user_id, older_than_hours=24):
                return 0

        # 正常情况
        backend = TestBackend({"bucket": "test"})
        backend._test_connection()  # 应该不抛异常

    def test_custom_backend_implementation(self):
        """测试自定义后端实现"""

        class CustomStorageBackend(BaseStorageBackend):
            """自定义存储后端用于测试"""

            def _validate_config(self, config):
                """自定义验证"""
                super()._validate_config(config)
                if not config.get("custom_field"):
                    raise ValueError("缺少 custom_field")
                return config

            def _create_client(self):
                """创建模拟客户端"""
                return MagicMock()

            async def upload_file(self, local_path, user_id, chat_id, channel_type):
                return f"mock://uri/{local_path}"

            async def download_file(self, uri, local_path):
                pass

            async def delete_chat_files(self, user_id, chat_id, channel_type, older_than_hours=None):
                return 0

            async def delete_user_files(self, user_id, older_than_hours=24):
                return 0

        # 测试实例化
        config = {
            "custom_field": "test_value",
            "bucket": "test_bucket"
        }
        backend = CustomStorageBackend(config)

        # 验证配置验证被调用
        assert backend.config["custom_field"] == "test_value"

        # 验证客户端创建
        assert backend._client is None  # 懒加载，未调用 _get_client
        client = backend._get_client()
        assert client is not None


class TestIntegration:
    """集成测试"""

    def test_factory_imports(self):
        """测试工厂导入"""
        from jiuwenclaw.storage import StorageService
        from jiuwenclaw.storage.factory import StorageService as FactoryService

        assert StorageService is FactoryService

    def test_all_exports(self):
        """测试导出完整性"""
        from jiuwenclaw import storage

        # 验证核心导出
        assert hasattr(storage, 'BaseStorageBackend')
        assert hasattr(storage, 'StorageBackend')  # 向后兼容
        assert hasattr(storage, 'StorageService')

        # 验证异常导出
        assert hasattr(storage, 'StorageError')
        assert hasattr(storage, 'UploadError')
        assert hasattr(storage, 'DownloadError')
        assert hasattr(storage, 'ConfigError')


class TestCustomStorageBackend:
    """测试自定义存储后端"""

    def test_custom_backend_inheritance(self):
        """测试自定义后端继承"""

        class CustomStorageBackend(BaseStorageBackend):
            """自定义存储后端用于测试"""

            def _validate_config(self, config):
                """自定义验证"""
                super()._validate_config(config)
                if not config.get("custom_field"):
                    raise ValueError("缺少 custom_field")
                return config

            def _create_client(self):
                """创建模拟客户端"""
                return MagicMock()

            async def upload_file(self, local_path, user_id, chat_id, channel_type):
                return f"mock://uri/{local_path}"

            async def download_file(self, uri, local_path):
                pass

            async def delete_chat_files(self, user_id, chat_id, channel_type, older_than_hours=None):
                return 0

            async def delete_user_files(self, user_id, older_than_hours=24):
                return 0

        # 测试实例化
        config = {
            "custom_field": "test_value",
            "bucket": "test_bucket"
        }
        backend = CustomStorageBackend(config)

        # 验证配置验证被调用
        assert backend.config["custom_field"] == "test_value"

        # 验证客户端创建
        assert backend._client is None  # 懒加载，未调用 _get_client
        client = backend._get_client()
        assert client is not None

    def test_custom_backend_validation_error(self):
        """测试自定义验证失败"""

        class CustomStorageBackend(BaseStorageBackend):
            def _validate_config(self, config):
                super()._validate_config(config)
                if not config.get("required_field"):
                    raise ValueError("缺少 required_field")
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

        # 测试验证失败
        with pytest.raises(ValueError, match="缺少 required_field"):
            CustomStorageBackend({"bucket": "test"})


class TestIntegration:
    """集成测试"""

    def test_factory_imports(self):
        """测试工厂导入"""
        from jiuwenclaw.storage import StorageService
        from jiuwenclaw.storage.factory import StorageService as FactoryService

        assert StorageService is FactoryService

    def test_all_exports(self):
        """测试导出完整性"""
        from jiuwenclaw import storage

        # 验证核心导出
        assert hasattr(storage, 'BaseStorageBackend')
        assert hasattr(storage, 'StorageBackend')  # 向后兼容
        assert hasattr(storage, 'StorageService')

        # 验证异常导出
        assert hasattr(storage, 'StorageError')
        assert hasattr(storage, 'UploadError')
        assert hasattr(storage, 'DownloadError')
        assert hasattr(storage, 'ConfigError')