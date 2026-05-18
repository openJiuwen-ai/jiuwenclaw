# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""本地文件系统存储后端 - SDK 内置默认 Backend.

功能特性：
1. 本地文件系统存储（无需云服务）
2. 路径安全验证（防止路径穿越攻击）
3. 自动清理过期文件
4. 开发环境、单机部署场景适用

配置示例：
  storage:
    type: "local"
    root_path: "/tmp/jiuwenclaw-storage"  # 可选，默认 ~/.jiuwenclaw/storage
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
from typing import Optional

from jiuwenclaw.storage.backend import BaseStorageBackend
from jiuwenclaw.storage.exceptions import UploadError, DownloadError, ConfigError
from jiuwenclaw.storage.utils import sanitize_chat_id, build_object_key


class LocalStorageBackend(BaseStorageBackend):
    """本地文件系统存储后端.

    特点：
    - 无需云服务账号（ak/sk）
    - 文件存储在本地 root_path 目录
    - 返回 file:// URI 格式
    - 路径安全验证（防止 ../../etc/passwd 等路径穿越攻击）

    配置字段：
    - root_path: 存储根目录（可选，默认 /tmp/jiuwenclaw-storage）
    """

    def _validate_config(self, config: dict) -> dict:
        """验证配置并设置 root_path.

        Args:
            config: 原始配置字典

        Returns:
            验证后的配置字典

        Raises:
            ConfigError: root_path 创建失败（权限不足等）
        """
        config = super()._validate_config(config)

        # 设置 root_path，默认 /tmp/jiuwenclaw-storage
        root_path_str = config.get("root_path", "/tmp/jiuwenclaw-storage")
        self.root_path = Path(root_path_str).resolve()

        # 创建 root_path 目录
        try:
            self.root_path.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"LocalStorageBackend root_path: {self.root_path}")
        except PermissionError as e:
            raise ConfigError(
                f"无法创建 root_path 目录 '{root_path_str}'，权限不足: {e}"
            ) from e
        except Exception as e:
            raise ConfigError(
                f"创建 root_path 目录 '{root_path_str}' 失败: {e}"
            ) from e

        return config

    def _create_client(self):
        """创建客户端 - 本地存储无需客户端对象.

        Returns:
            self 对象本身（本地存储直接操作文件系统）
        """
        return self

    def _validate_path_security(self, target_path: Path) -> None:
        """路径安全验证 - 防止路径穿越攻击.

        使用白名单验证：
        1. Path.resolve() 解析绝对路径（包括符号链接）
        2. 验证最终路径必须在 root_path 范围内

        Args:
            target_path: 目标文件路径

        Raises:
            UploadError: 如果路径解析后超出 root_path 范围（路径穿越攻击）
        """
        resolved_path = target_path.resolve()

        if not str(resolved_path).startswith(str(self.root_path)):
            raise UploadError(
                f"路径穿越攻击：文件路径超出 root_path 范围。"
                f"target_path={target_path}, resolved={resolved_path}, root_path={self.root_path}"
            )

    async def upload_file(
        self,
        local_path: str,
        user_id: str,
        chat_id: str,
        channel_type: str
    ) -> str:
        """上传文件 - 复制本地文件到 root_path 目录.

        Args:
            local_path: 本地文件路径
            user_id: 用户 ID
            chat_id: 会话 ID
            channel_type: 渠道类型

        Returns:
            file:// URI 格式的文件路径

        Raises:
            UploadError: 文件不存在、路径验证失败、复制失败
        """
        # 检查本地文件是否存在
        source_path = Path(local_path)
        if not source_path.exists():
            raise UploadError(f"本地文件不存在: {local_path}")

        # 清理 chat_id 特殊字符
        cleaned_chat_id = sanitize_chat_id(chat_id, channel_type)

        # 构建目标路径
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = source_path.name
        object_key = build_object_key(user_id, channel_type, cleaned_chat_id, timestamp, filename)
        target_path = self.root_path / object_key

        # 路径安全验证
        self._validate_path_security(target_path)

        # 创建目标目录
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # 复制文件
        try:
            shutil.copy2(source_path, target_path)
            self.logger.info(
                f"文件上传成功: {local_path} -> {target_path} "
                f"(user={user_id}, channel={channel_type}, chat={chat_id})"
            )
        except Exception as e:
            raise UploadError(f"文件复制失败: {local_path} -> {target_path}: {e}") from e

        # 返回 file:// URI
        return f"file://{target_path}"

    async def download_file(self, uri: str, local_path: str) -> None:
        """下载文件 - 从 root_path 复制文件到本地 workspace.

        Args:
            uri: file:// URI 格式的文件路径
            local_path: 本地目标路径

        Raises:
            DownloadError: URI 格式不支持、源文件不存在、复制失败
        """
        # 检查 URI 格式
        if not uri.startswith("file://"):
            raise DownloadError(
                f"LocalStorageBackend 不支持非 file:// URI: {uri}"
            )

        # 提取源文件路径
        source_path_str = uri[7:]  # 移除 "file://" 前缀
        source_path = Path(source_path_str)

        # 检查源文件是否存在
        if not source_path.exists():
            raise DownloadError(f"源文件不存在: {uri}")

        # 创建本地目标目录
        target_path = Path(local_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # 复制文件
        try:
            shutil.copy2(source_path, target_path)
            self.logger.info(f"文件下载成功: {uri} -> {local_path}")
        except Exception as e:
            raise DownloadError(f"文件复制失败: {uri} -> {local_path}: {e}") from e

    async def delete_chat_files(
        self,
        user_id: str,
        chat_id: str,
        channel_type: str,
        older_than_hours: Optional[int] = None
    ) -> int:
        """删除会话文件 - 删除指定 chat 目录下的所有文件.

        Args:
            user_id: 用户 ID
            chat_id: 会话 ID
            channel_type: 渠道类型
            older_than_hours: 删除多少小时前的文件（可选，默认删除全部）

        Returns:
            删除的文件数量
        """
        # 清理 chat_id
        cleaned_chat_id = sanitize_chat_id(chat_id, channel_type)

        # 构建 chat 目录路径
        from jiuwenclaw.storage.utils import build_chat_prefix
        chat_prefix = build_chat_prefix(channel_type, cleaned_chat_id)
        chat_dir = self.root_path / "files" / user_id / chat_prefix

        if not chat_dir.exists():
            self.logger.info(f"chat 目录不存在，无需删除: {chat_dir}")
            return 0

        # 统计并删除文件
        deleted_count = 0
        if older_than_hours is None:
            # 删除整个 chat 目录
            try:
                file_count = sum(1 for _ in chat_dir.rglob("*") if _.is_file())
                shutil.rmtree(chat_dir)
                deleted_count = file_count
                self.logger.info(
                    f"删除 chat 目录: {chat_dir}, 文件数量={deleted_count} "
                    f"(user={user_id}, channel={channel_type}, chat={chat_id})"
                )
            except Exception as e:
                self.logger.error(f"删除 chat 目录失败: {chat_dir}: {e}")
        else:
            # 删除指定时间前的文件
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)

            for file_path in chat_dir.rglob("*"):
                if file_path.is_file():
                    # 检查文件创建时间
                    file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
                    if file_mtime < cutoff_time:
                        try:
                            file_path.unlink()
                            deleted_count += 1
                            self.logger.debug(f"删除过期文件: {file_path}")
                        except Exception as e:
                            self.logger.error(f"删除文件失败: {file_path}: {e}")

            self.logger.info(
                f"删除 chat 过期文件: {chat_dir}, 文件数量={deleted_count} "
                f"(older_than={older_than_hours}h, user={user_id}, chat={chat_id})"
            )

        return deleted_count

    async def delete_user_files(
        self,
        user_id: str,
        older_than_hours: int = 24
    ) -> int:
        """删除用户文件 - 删除用户目录下所有过期文件.

        Args:
            user_id: 用户 ID
            older_than_hours: 删除多少小时前的文件（默认 24 小时）

        Returns:
            删除的文件数量
        """
        # 构建用户目录路径
        user_dir = self.root_path / "files" / user_id

        if not user_dir.exists():
            self.logger.info(f"用户目录不存在，无需删除: {user_dir}")
            return 0

        # 删除过期文件
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)

        deleted_count = 0
        for file_path in user_dir.rglob("*"):
            if file_path.is_file():
                # 检查文件创建时间
                file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
                if file_mtime < cutoff_time:
                    try:
                        file_path.unlink()
                        deleted_count += 1
                        self.logger.debug(f"删除过期文件: {file_path}")
                    except Exception as e:
                        self.logger.error(f"删除文件失败: {file_path}: {e}")

        self.logger.info(
            f"删除用户过期文件: {user_dir}, 文件数量={deleted_count} "
            f"(older_than={older_than_hours}h, user={user_id})"
        )

        return deleted_count