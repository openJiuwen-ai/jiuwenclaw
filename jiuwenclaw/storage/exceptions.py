# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""存储服务异常定义。"""


class StorageError(Exception):
    """存储服务基础异常。"""
    pass


class StorageFileNotFoundError(StorageError):
    """文件不存在。"""
    pass


class StoragePermissionError(StorageError):
    """访问权限不足。"""
    pass


class UploadError(StorageError):
    """上传失败。"""
    pass


class DownloadError(StorageError):
    """下载失败。"""
    pass


class ConfigError(StorageError):
    """配置错误。"""
    pass
