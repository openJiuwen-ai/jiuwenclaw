# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""存储服务工厂类 - 使用 Registry 模式实现非入侵式 Backend 扩展."""

import logging

from jiuwenclaw.storage.backend import BaseStorageBackend
from jiuwenclaw.storage.exceptions import ConfigError
from jiuwenclaw.storage.registry import StorageBackendRegistry

logger = logging.getLogger(__name__)


class StorageService:
    """存储服务工厂类（单例）.

    核心改进：
    1. 使用 Registry 查找 Backend 类（非入侵式）
    2. 配置驱动自动注册（backend_class 字段）
    3. 用户无需修改 SDK 源码，只需继承 BaseStorageBackend + 配置即可

    使用方法：
        # config.yaml:
        #   storage:
        #     type: "my-obs"
        #     backend_class: "myapp.storage.MyObsBackend"
        #     access_key: "..."
        #     secret_key: "..."

        # StorageService 自动加载并注册 MyObsBackend
    """

    _instance = None

    @classmethod
    async def get_instance(cls) -> BaseStorageBackend:
        """获取存储服务实例（单例）.

        Returns:
            BaseStorageBackend: 存储后端实例

        Raises:
            ConfigError: 配置错误
        """
        if cls._instance is None:
            cls._instance = await cls._create_backend()
        return cls._instance

    @classmethod
    async def _create_backend(cls) -> BaseStorageBackend:
        """根据配置创建 Backend 实例 - 使用 Registry 模式.

        流程：
        1. 加载配置（storage section）
        2. 调用 Registry.register_from_config(config) 执行配置驱动注册
        3. 调用 Registry.get(backend_type) 查找 Backend 类
        4. 实例化 Backend 对象

        Returns:
            BaseStorageBackend: 存储后端实例

        Raises:
            ConfigError: 配置错误或 Backend 未注册
        """
        try:
            from jiuwenclaw.config import get_config

            config = get_config()
            storage_config = config.get("storage", {})
            backend_type = storage_config.get("type", "local")

            logger.info(f"Creating storage backend of type: {backend_type}")

            # 配置驱动自动注册（如果配置中包含 backend_class 字段）
            StorageBackendRegistry.register_from_config(storage_config)

            # 查找 Backend 类
            backend_class = StorageBackendRegistry.get(backend_type)
            if backend_class is None:
                # Backend 未注册
                available_backends = StorageBackendRegistry.list_available()
                raise ConfigError(
                    f"Backend '{backend_type}' 未注册。"
                    f"可用 Backend: {available_backends}。"
                    f"请检查 config.yaml 配置或调用 StorageBackendRegistry.register() 手动注册。"
                )

            # 实例化 Backend 对象
            backend = backend_class(storage_config)
            logger.info(
                f"Storage backend created successfully: "
                f"type={backend_type}, class={backend_class.__name__}"
            )
            return backend

        except ConfigError:
            # 重新抛出 ConfigError
            raise
        except Exception as e:
            logger.error(f"Failed to create storage backend: {e}")
            raise ConfigError(f"Failed to create storage backend: {e}") from e

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例实例（主要用于测试）.

        注意：此方法会清空已创建的实例，下次调用 get_instance 时会重新创建。
        """
        cls._instance = None
        logger.info("Storage service instance reset")
