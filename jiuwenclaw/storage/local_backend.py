# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""本地文件系统存储后端实现。"""

import asyncio
import logging
import shutil
from pathlib import Path

import aiohttp

from jiuwenclaw.storage.backend import StorageBackend
from jiuwenclaw.storage.exceptions import DownloadError, UploadError

logger = logging.getLogger(__name__)


class LocalStorageBackend(StorageBackend):
    """
    本地文件系统作为"对象存储"。

    实际上是通过HTTP从本地存储服务下载，
    或者直接访问本地文件系统（file://协议）。
    """

    def __init__(self, config: dict):
        self.base_dir = Path(config["base_dir"]).expanduser()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"LocalStorageBackend initialized with base_dir: {self.base_dir}")

    async def download_file(self, uri: str, local_path: str) -> None:
        """从本地存储服务或文件系统下载。

        Args:
            uri: 文件URI（支持 file://, http://, https://）
            local_path: 本地保存路径

        Raises:
            DownloadError: 下载失败
        """
        try:
            # 确保目标目录存在
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)

            # 处理不同的URI协议
            if uri.startswith("file://"):
                # 本地文件系统，直接复制
                await self._download_from_filesystem(uri, local_path)
            elif uri.startswith(("http://", "https://")):
                # 本地存储服务，通过HTTP下载
                await self._download_from_http(uri, local_path)
            else:
                raise DownloadError(f"Unsupported URI scheme: {uri}")

            logger.info(f"Successfully downloaded file from {uri} to {local_path}")

        except Exception as e:
            logger.error(f"Failed to download file from {uri}: {e}")
            raise DownloadError(f"Download failed: {e}") from e

    async def _download_from_filesystem(self, uri: str, local_path: str) -> None:
        """从本地文件系统复制文件。

        Args:
            uri: file:// URI
            local_path: 目标路径
        """
        # 移除 file:// 前缀
        src = Path(uri[7:])
        if not src.exists():
            raise DownloadError(f"File not found: {src}")

        # 复制文件
        shutil.copy2(src, local_path)

    async def _download_from_http(self, uri: str, local_path: str) -> None:
        """通过HTTP下载文件。

        Args:
            uri: HTTP/HTTPS URI
            local_path: 目标路径
        """
        async with aiohttp.ClientSession() as session:
            async with session.get(uri) as response:
                if response.status != 200:
                    raise DownloadError(f"HTTP download failed with status {response.status}")

                content = await response.read()

                # 写入文件
                with open(local_path, 'wb') as f:
                    f.write(content)

    async def upload_file(self, local_path: str, user_id: str) -> str:
        """上传文件到本地存储服务。

        Args:
            local_path: 本地文件路径
            user_id: 用户ID

        Returns:
            对象存储URI（http://... 或 file://...）

        Raises:
            UploadError: 上传失败
        """
        try:
            from datetime import datetime, timezone

            # 确保文件存在
            if not Path(local_path).exists():
                raise UploadError(f"Local file not found: {local_path}")

            # 构建对象Key（USER_ID + 时间戳）
            # 使用 UTC 时间确保跨时区一致性
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = Path(local_path).name
            object_key = f"files/{user_id}/{timestamp}/{filename}"

            # 对于本地存储，返回 file:// URI
            # 实际文件应该保存在配置的 upload_dir 中
            # 这里简化处理，直接返回本地文件URI
            uri = f"file://{local_path}"

            logger.info(f"Uploaded file {local_path} as {uri}")
            return uri

        except Exception as e:
            logger.error(f"Failed to upload file {local_path}: {e}")
            raise UploadError(f"Upload failed: {e}") from e
