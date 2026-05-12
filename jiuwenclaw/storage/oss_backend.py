# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""阿里云 OSS 存储后端实现。"""

import logging
from pathlib import Path

from jiuwenclaw.storage.backend import StorageBackend
from jiuwenclaw.storage.exceptions import DownloadError, UploadError

logger = logging.getLogger(__name__)


class OssStorageBackend(StorageBackend):
    """
    阿里云对象存储服务（OSS）后端。

    使用 oss2 SDK 与阿里云 OSS 交互。
    """

    def __init__(self, config: dict):
        """初始化 OSS 后端.

        Args:
            config: 配置字典，包含：
                - access_key: OSS 访问密钥 ID
                - secret_key: OSS 访问密钥
                - endpoint: OSS 终端节点（如 oss-cn-hangzhou.aliyuncs.com）
                - bucket: OSS 桶名称
        """
        self.access_key = config.get("access_key")
        self.secret_key = config.get("secret_key")
        self.endpoint = config.get("endpoint", "oss-cn-hangzhou.aliyuncs.com")
        self.bucket = config.get("bucket")

        if not all([self.access_key, self.secret_key, self.bucket]):
            raise ValueError(
                "OSS 配置不完整，需要提供：access_key, secret_key, bucket"
            )

        # 懒加载 OSS SDK（避免未安装时报错）
        self._bucket = None
        logger.info(
            f"OssStorageBackend initialized: bucket={self.bucket}, endpoint={self.endpoint}"
        )

    def _get_bucket(self):
        """获取 OSS Bucket 对象（懒初始化）."""
        if self._bucket is not None:
            return self._bucket

        try:
            import oss2

            # 创建 Auth 实例
            auth = oss2.Auth(self.access_key, self.secret_key)

            # 创建 Bucket 实例
            self._bucket = oss2.Bucket(auth, self.endpoint, self.bucket)

            # 验证连接
            self._bucket.get_bucket_info()

            logger.info("OSS bucket created successfully")
            return self._bucket

        except ImportError:
            raise ImportError(
                "未安装 oss2 SDK，请运行：pip install oss2"
            ) from None
        except Exception as e:
            raise UploadError(f"创建 OSS Bucket 失败: {e}") from e

    async def download_file(self, uri: str, local_path: str) -> None:
        """从 OSS 下载文件到本地.

        Args:
            uri: OSS URI（格式：https://{bucket}.oss-{region}.aliyuncs.com/{key}）
            local_path: 本地保存路径

        Raises:
            DownloadError: 下载失败
        """
        try:
            # 解析 URI 获取对象 Key
            object_key = self._parse_uri(uri)

            # 确保目标目录存在
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)

            # 获取 OSS Bucket
            bucket = self._get_bucket()

            # 下载文件
            bucket.get_object_to_file(object_key, local_path)

            logger.info(f"Successfully downloaded file from OSS: {object_key} -> {local_path}")

        except Exception as e:
            logger.error(f"Failed to download file from OSS {uri}: {e}")
            raise DownloadError(f"OSS 下载失败: {e}") from e

    async def upload_file(self, local_path: str, user_id: str) -> str:
        """上传文件到 OSS.

        Args:
            local_path: 本地文件路径
            user_id: 用户 ID（用于路径隔离）

        Returns:
            OSS URI（格式：https://{bucket}.oss-{region}.aliyuncs.com/{key}）

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

            # 获取 OSS Bucket
            bucket = self._get_bucket()

            # 上传文件
            with open(local_path, 'rb') as file_obj:
                result = bucket.put_object(object_key, file_obj)

            if result.status != 200:
                raise UploadError(
                    f"OSS 上传失败: status={result.status}, message={result.status}"
                )

            # 构建返回 URI
            uri = f"https://{self.bucket}.{self.endpoint}/{object_key}"

            logger.info(f"Successfully uploaded file to OSS: {local_path} -> {uri}")
            return uri

        except Exception as e:
            logger.error(f"Failed to upload file {local_path} to OSS: {e}")
            raise UploadError(f"OSS 上传失败: {e}") from e

    def _parse_uri(self, uri: str) -> str:
        """从 OSS URI 解析对象 Key.

        Args:
            uri: OSS URI（格式：https://{bucket}.oss-{region}.aliyuncs.com/{key}）

        Returns:
            对象 Key

        Raises:
            DownloadError: URI 格式错误
        """
        try:
            # 支持标准 URI 格式
            # https://bucket.oss-cn-hangzhou.aliyuncs.com/key

            prefix = f"https://{self.bucket}.{self.endpoint}/"
            if uri.startswith(prefix):
                return uri[len(prefix):]

            else:
                raise DownloadError(f"不支持的 OSS URI 格式: {uri}")

        except Exception as e:
            raise DownloadError(f"解析 OSS URI 失败: {e}") from e
