# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""华为云 OBS 存储后端实现。"""

import logging
from pathlib import Path

from jiuwenclaw.storage.backend import StorageBackend
from jiuwenclaw.storage.exceptions import DownloadError, UploadError

logger = logging.getLogger(__name__)


class ObsStorageBackend(StorageBackend):
    """
    华为云对象存储服务（OBS）后端。

    使用 esdk-obs-py SDK 与华为云 OBS 交互。
    """

    def __init__(self, config: dict):
        """初始化 OBS 后端.

        Args:
            config: 配置字典，包含：
                - access_key: OBS 访问密钥 ID
                - secret_key: OBS 访问密钥
                - endpoint: OBS 终端节点（如 obs.cn-north-4.myhuaweicloud.com）
                - bucket: OBS 桶名称
        """
        self.access_key = config.get("access_key")
        self.secret_key = config.get("secret_key")
        self.endpoint = config.get("endpoint", "obs.cn-north-4.myhuaweicloud.com")
        self.bucket = config.get("bucket")

        if not all([self.access_key, self.secret_key, self.bucket]):
            raise ValueError(
                "OBS 配置不完整，需要提供：access_key, secret_key, bucket"
            )

        # 懒加载 OBS SDK（避免未安装时报错）
        self._client = None
        logger.info(
            f"ObsStorageBackend initialized: bucket={self.bucket}, endpoint={self.endpoint}"
        )

    def _get_client(self):
        """获取 OBS 客户端（懒初始化）."""
        if self._client is not None:
            return self._client

        try:
            from obs import ObsClient

            self._client = ObsClient(
                access_key_id=self.access_key,
                secret_access_key=self.secret_key,
                server=self.endpoint,
            )
            logger.info("OBS client created successfully")
            return self._client

        except ImportError:
            raise ImportError(
                "未安装 esdk-obs-py SDK，请运行：pip install esdk-obs-py"
            ) from None
        except Exception as e:
            raise UploadError(f"创建 OBS 客户端失败: {e}") from e

    async def download_file(self, uri: str, local_path: str) -> None:
        """从 OBS 下载文件到本地.

        Args:
            uri: OBS URI（格式：https://obs.{region}.myhuaweicloud.com/{bucket}/{key}）
            local_path: 本地保存路径

        Raises:
            DownloadError: 下载失败
        """
        try:
            # 解析 URI 获取对象 Key
            object_key = self._parse_uri(uri)

            # 确保目标目录存在
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)

            # 获取 OBS 客户端
            client = self._get_client()

            # 下载文件
            resp = client.getObject(self.bucket, object_key, downloadPath=local_path)

            if resp.status < 200 or resp.status >= 300:
                raise DownloadError(
                    f"OBS 下载失败: status={resp.status}, errorCode={resp.errorCode}, "
                    f"message={resp.errorMessage}"
                )

            logger.info(f"Successfully downloaded file from OBS: {object_key} -> {local_path}")

        except Exception as e:
            logger.error(f"Failed to download file from OBS {uri}: {e}")
            raise DownloadError(f"OBS 下载失败: {e}") from e

    async def upload_file(self, local_path: str, user_id: str) -> str:
        """上传文件到 OBS.

        Args:
            local_path: 本地文件路径
            user_id: 用户 ID（用于路径隔离）

        Returns:
            OBS URI（格式：https://obs.{region}.myhuaweicloud.com/{bucket}/{key}）

        Raises:
            UploadError: 上传失败
        """
        try:
            from datetime import datetime, timezone

            # 确保文件存在
            if not Path(local_path).exists():
                raise UploadError(f"本地文件不存在: {local_path}")

            # 构建对象 Key（USER_ID + 时间戳）
            # 使用 UTC 时间确保跨时区一致性
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = Path(local_path).name
            object_key = f"files/{user_id}/{timestamp}/{filename}"

            # 获取 OBS 客户端
            client = self._get_client()

            # 上传文件
            resp = client.putFile(self.bucket, object_key, local_path)

            if resp.status < 200 or resp.status >= 300:
                raise UploadError(
                    f"OBS 上传失败: status={resp.status}, errorCode={resp.errorCode}, "
                    f"message={resp.errorMessage}"
                )

            # 构建返回 URI
            uri = f"https://{self.bucket}.{self.endpoint}/{object_key}"

            logger.info(f"Successfully uploaded file to OBS: {local_path} -> {uri}")
            return uri

        except Exception as e:
            logger.error(f"Failed to upload file {local_path} to OBS: {e}")
            raise UploadError(f"OBS 上传失败: {e}") from e

    def _parse_uri(self, uri: str) -> str:
        """从 OBS URI 解析对象 Key.

        Args:
            uri: OBS URI（格式：https://obs.{region}.myhuaweicloud.com/{bucket}/{key}）

        Returns:
            对象 Key

        Raises:
            DownloadError: URI 格式错误
        """
        try:
            # 支持多种 URI 格式
            # https://bucket.obs.cn-north-4.myhuaweicloud.com/key
            # https://obs.cn-north-4.myhuaweicloud.com/bucket/key

            if uri.startswith(f"https://{self.bucket}.{self.endpoint}/"):
                # 格式：https://bucket.obs.../key
                prefix = f"https://{self.bucket}.{self.endpoint}/"
                return uri[len(prefix):]

            elif uri.startswith(f"https://{self.endpoint}/{self.bucket}/"):
                # 格式：https://obs.../bucket/key
                prefix = f"https://{self.endpoint}/{self.bucket}/"
                return uri[len(prefix):]

            else:
                raise DownloadError(f"不支持的 OBS URI 格式: {uri}")

        except Exception as e:
            raise DownloadError(f"解析 OBS URI 失败: {e}") from e
