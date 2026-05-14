# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""存储服务工厂类。"""

import logging

from jiuwenclaw.storage.backend import BaseStorageBackend
from jiuwenclaw.storage.exceptions import ConfigError

logger = logging.getLogger(__name__)


class StorageService:
    """
    存储服务工厂类（单例）。

    注意：具体实现已移至文档，商用场景需要自己实现继承 BaseStorageBackend。

    详细实现示例请参考：
    - docs/zh/对象存储接口设计.md - 完整的设计文档和实现示例
    - jiuwenclaw/storage/COMMERCIAL_EXAMPLES.md - 商用实现详细示例
    """

    _instance = None

    @classmethod
    async def get_instance(cls) -> BaseStorageBackend:
        """获取存储服务实例（单例）。

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
        """
        根据配置创建后端。

        注意：这里只提供框架，具体实现需要根据项目需求自行实现。

        示例实现请参考：
        - docs/zh/对象存储接口设计.md - LocalStorageBackend/ObsStorageBackend/OssStorageBackend 实现示例
        - jiuwenclaw/storage/COMMERCIAL_EXAMPLES.md - AWS S3/Azure Blob/腾讯云COS 等商用实现

        Returns:
            BaseStorageBackend: 存储后端实例

        Raises:
            ConfigError: 配置错误或不支持的存储类型
        """
        try:
            from jiuwenclaw.config import get_config

            config = get_config()
            storage_config = config.get("storage", {})
            backend_type = storage_config.get("type", "local")

            logger.info(f"Creating storage backend of type: {backend_type}")

            # 注意：以下代码仅为示例框架
            # 具体实现请参考 docs/zh/对象存储接口设计.md

            if backend_type == "local":
                # 本地存储实现示例
                # 请参考 docs/zh/对象存储接口设计.md 中的 LocalStorageBackend 实现
                raise NotImplementedError(
                    "本地存储实现请参考 docs/zh/对象存储接口设计.md 中的 LocalStorageBackend 示例代码"
                )

            elif backend_type == "huawei-obs":
                # 华为云 OBS 实现示例
                # 请参考 docs/zh/对象存储接口设计.md 中的 ObsStorageBackend 实现
                raise NotImplementedError(
                    "华为云 OBS 实现请参考 docs/zh/对象存储接口设计.md 中的 ObsStorageBackend 示例代码"
                )

            elif backend_type == "aliyun-oss":
                # 阿里云 OSS 实现示例
                # 请参考 docs/zh/对象存储接口设计.md 中的 OssStorageBackend 实现
                raise NotImplementedError(
                    "阿里云 OSS 实现请参考 docs/zh/对象存储接口设计.md 中的 OssStorageBackend 示例代码"
                )

            else:
                raise ConfigError(
                    f"Unknown storage type: {backend_type}. "
                    f"如需自定义实现，请继承 BaseStorageBackend 并参考文档示例。"
                )

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
