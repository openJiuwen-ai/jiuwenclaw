# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""存储后端抽象接口定义。"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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


class StorageBackend(ABC):
    """
    对象存储后端抽象接口。

    核心职责：
    1. download_file(): 从对象存储URI下载文件到本地workspace
    2. upload_file(): 上传本地文件到对象存储，返回URI

    设计原则：
    - 简单：只有两个核心方法
    - 内部：仅AgentServer内部使用
    - 无状态：不维护会话状态
    """

    @abstractmethod
    async def download_file(
        self,
        uri: str,  # 对象存储URI（来自E2AEnvelope.params.files）
        local_path: str,  # 本地保存路径（Agent workspace）
    ) -> None:
        """从对象存储下载文件到本地workspace。

        参数:
            uri: 对象存储URI
                - https://obs... (华为云OBS)
                - https://oss... (阿里云OSS)
                - http://... (本地存储服务)
                - file://... (本地文件系统)
            local_path: 本地保存路径

        异常:
            StorageFileNotFoundError: 源文件不存在
            StoragePermissionError: 访问权限不足
            StorageError: 存储服务错误

        使用场景:
            AgentServer收到E2AEnvelope.params.files后，
            需要下载文件到workspace供Agent使用
        """
        pass

    @abstractmethod
    async def upload_file(
        self,
        local_path: str,  # Agent生成的本地文件
        user_id: str,  # 用户ID（用于路径隔离）
    ) -> str:  # 返回对象存储URI
        """上传本地文件到对象存储。

        参数:
            local_path: 本地文件路径
            user_id: 用户ID

        返回:
            对象存储URI（https://obs:// 或 https://oss://）

        异常:
            StorageFileNotFoundError: 本地文件不存在
            StorageError: 上传失败

        使用场景:
            Agent生成文件后，需要上传到对象存储
            URI通过E2AResponse返回给前端
        """
        pass
