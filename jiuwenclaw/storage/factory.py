# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""存储服务工厂类。"""

import logging

from jiuwenclaw.storage.backend import StorageBackend
from jiuwenclaw.storage.exceptions import ConfigError
from jiuwenclaw.storage.local_backend import LocalStorageBackend

logger = logging.getLogger(__name__)


class StorageService:
    """存储服务工厂类（单例）。"""

    _instance = None

    @classmethod
    async def get_instance(cls) -> StorageBackend:
        """获取存储服务实例（单例）。

        Returns:
            StorageBackend: 存储后端实例

        Raises:
            ConfigError: 配置错误
        """
        if cls._instance is None:
            cls._instance = await cls._create_backend()
        return cls._instance

    @classmethod
    async def _create_backend(cls) -> StorageBackend:
        """根据配置创建后端。

        Returns:
            StorageBackend: 存储后端实例

        Raises:
            ConfigError: 配置错误或不支持的存储类型
        """
        try:
            from jiuwenclaw.config import get_config

            config = get_config()
            storage_config = config.get("storage", {})
            backend_type = storage_config.get("type", "local")

            logger.info(f"Creating storage backend of type: {backend_type}")

            if backend_type == "local":
                local_config = storage_config.get("local", {})
                return LocalStorageBackend(local_config)

            elif backend_type == "huawei-obs":
                from jiuwenclaw.storage.obs_backend import ObsStorageBackend

                obs_config = storage_config.get("huawei_obs", {})
                return ObsStorageBackend(obs_config)

            elif backend_type == "aliyun-oss":
                from jiuwenclaw.storage.oss_backend import OssStorageBackend

                oss_config = storage_config.get("aliyun_oss", {})
                return OssStorageBackend(oss_config)

            else:
                raise ConfigError(f"Unknown storage type: {backend_type}")

        except Exception as e:
            logger.error(f"Failed to create storage backend: {e}")
            raise ConfigError(f"Failed to create storage backend: {e}") from e

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例实例（主要用于测试）。

        注意：此方法会清空已创建的实例，下次调用 get_instance 时会重新创建。
        """
        cls._instance = None
        logger.info("Storage service instance reset")
