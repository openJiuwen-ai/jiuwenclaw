# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""存储后端抽象接口定义。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
import logging


@dataclass
class FileRef:
    """文件引用数据类。

    用于表示对象存储中的文件引用。
    """

    uri: str  # 对象存储URI
    name: str | None = None  # 文件名
    mime_type: str | None = None  # MIME类型
    size: int | None = None  # 文件大小（字节）
    meta: dict[str, Any] = field(default_factory=dict)  # 扩展元数据


class BaseStorageBackend(ABC):
    """
    🎯 简化的存储后端抽象基类 - 统一配置验证和客户端管理

    核心改进：
    1. 统一的配置验证（ak/sk 校验）
    2. 模板方法模式减少重复代码
    3. 懒加载客户端 + 连接测试
    4. 向后兼容现有实现

    使用方法：
        class MyStorageBackend(BaseStorageBackend):
            def _validate_config(self, config):
                # 自定义验证逻辑
                super()._validate_config(config)
                return config

            def _create_client(self):
                # 创建客户端
                return MyClient(...)

            # 实现核心业务方法...
    """

    def __init__(self, config: dict):
        """统一初始化入口"""
        # 1. 配置验证
        self.config = self._validate_config(config)

        # 2. 基础属性设置
        self.access_key = self.config.get("access_key", "")
        self.secret_key = self.config.get("secret_key", "")
        self.bucket = self.config.get("bucket", "")
        self.endpoint = self.config.get("endpoint", "")
        self.region = self.config.get("region", "")

        # 3. 懒加载客户端
        self._client = None

        # 4. 日志
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.info(f"{self.__class__.__name__} initialized: bucket={self.bucket}")

    def _validate_config(self, config: dict) -> dict:
        """🔒 基础配置验证模板方法 - 子类可重写

        Args:
            config: 原始配置字典

        Returns:
            验证后的配置字典

        Raises:
            ValueError: 配置验证失败
        """
        if not isinstance(config, dict):
            raise ValueError("配置必须是字典类型")

        # 子类可以重写此方法添加特定验证
        return config

    def _get_client(self):
        """🔧 客户端获取模板方法（懒加载）"""
        if self._client is None:
            self._client = self._create_client()
            self._test_connection()  # 可选连接测试
        return self._client

    @abstractmethod
    def _create_client(self):
        """🏗️ 客户端创建抽象方法 - 子类必须实现

        Returns:
            存储服务客户端对象
        """
        pass

    def _test_connection(self):
        """🔌 连接测试模板方法 - 子类可重写"""
        pass

    @abstractmethod
    async def upload_file(
        self,
        local_path: str,
        user_id: str,
        chat_id: str,
        channel_type: str
    ) -> str:
        """上传文件 - 返回URI"""
        pass

    @abstractmethod
    async def download_file(self, uri: str, local_path: str) -> None:
        """下载文件"""
        pass

    @abstractmethod
    async def delete_chat_files(
        self,
        user_id: str,
        chat_id: str,
        channel_type: str,
        older_than_hours: Optional[int] = None
    ) -> int:
        """删除会话文件 - 返回删除数量"""
        pass

    @abstractmethod
    async def delete_user_files(
        self,
        user_id: str,
        older_than_hours: int = 24
    ) -> int:
        """删除用户文件 - 返回删除数量"""
        pass


# 向后兼容：保留旧名称作为别名
StorageBackend = BaseStorageBackend
