# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuwenClaw Storage Interface - AgentServer 内部存储服务.

提供统一的接口用于AgentServer与多种对象存储服务之间的文件交互。
支持本地文件系统、华为云OBS、阿里云OSS等存储后端。
"""

from jiuwenclaw.storage.backend import StorageBackend
from jiuwenclaw.storage.exceptions import (
    ConfigError,
    DownloadError,
    StorageFileNotFoundError,
    StoragePermissionError,
    StorageError,
    UploadError,
)
from jiuwenclaw.storage.factory import StorageService
from jiuwenclaw.storage.local_backend import LocalStorageBackend
from jiuwenclaw.storage.obs_backend import ObsStorageBackend
from jiuwenclaw.storage.oss_backend import OssStorageBackend

__all__ = [
    "StorageBackend",
    "StorageService",
    "LocalStorageBackend",
    "ObsStorageBackend",
    "OssStorageBackend",
    "StorageError",
    "StorageFileNotFoundError",
    "StoragePermissionError",
    "UploadError",
    "DownloadError",
    "ConfigError",
]
