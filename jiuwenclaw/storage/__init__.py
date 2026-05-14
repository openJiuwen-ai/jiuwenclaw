# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuwenClaw Storage Interface - AgentServer 内部存储服务.

提供统一的接口用于AgentServer与多种对象存储服务之间的文件交互。
支持本地文件系统、华为云OBS、阿里云OSS等存储后端。

具体实现请参考文档：docs/zh/对象存储接口设计.md
"""

from jiuwenclaw.storage.backend import BaseStorageBackend, StorageBackend
from jiuwenclaw.storage.exceptions import (
    ConfigError,
    DownloadError,
    StorageFileNotFoundError,
    StoragePermissionError,
    StorageError,
    UploadError,
)
from jiuwenclaw.storage.factory import StorageService

__all__ = [
    # 核心接口
    "BaseStorageBackend",  # 新的基类名称
    "StorageBackend",      # 向后兼容：别名
    "StorageService",      # 工厂类

    # 异常类
    "StorageError",
    "StorageFileNotFoundError",
    "StoragePermissionError",
    "UploadError",
    "DownloadError",
    "ConfigError",
]
