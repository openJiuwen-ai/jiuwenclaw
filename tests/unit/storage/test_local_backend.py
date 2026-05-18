# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

# pylint: disable=protected-access
# pylint: disable=no-self-argument
# 测试代码访问私有成员和不需要实例访问的测试方法是合理的测试实践

"""测试 LocalStorageBackend 路径安全验证和文件操作."""

import tempfile
from pathlib import Path
from unittest.mock import patch
import os
import time

import pytest

from jiuwenclaw.storage.backends.local import LocalStorageBackend
from jiuwenclaw.storage.exceptions import UploadError, StoragePermissionError


class TestLocalStorageBackendSecurity:
    """测试路径安全验证."""

    @staticmethod
    def test_path_traversal_attack_with_double_dot(tmp_path):
        """测试拒绝路径穿越攻击（../）."""
        # 创建 LocalStorageBackend
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }
        backend = LocalStorageBackend(config)

        # 尝试穿越到 storage_root 外部
        malicious_path = storage_root / "user1" / "chat1" / ".." / ".." / ".." / "etc" / "passwd"

        # 验证抛出 UploadError（路径穿越攻击）
        with pytest.raises(UploadError, match="路径穿越攻击"):
            backend._validate_path_security(malicious_path)

    @staticmethod
    def test_path_traversal_attack_absolute_path(tmp_path):
        """测试拒绝绝对路径穿越."""
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }
        backend = LocalStorageBackend(config)

        # 尝试访问外部绝对路径
        malicious_path = Path("/etc/passwd")

        # 验证抛出 UploadError（路径穿越攻击）
        with pytest.raises(UploadError, match="路径穿越攻击"):
            backend._validate_path_security(malicious_path)

    @staticmethod
    def test_symlink_path_traversal(tmp_path):
        """测试拒绝符号链接穿越."""
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        # 创建外部目录
        external_dir = tmp_path / "external"
        external_dir.mkdir(parents=True, exist_ok=True)
        external_file = external_dir / "secret.txt"
        external_file.write_text("secret data")

        # 在 storage_root 内创建符号链接指向外部目录
        user_dir = storage_root / "user1" / "chat1"
        user_dir.mkdir(parents=True, exist_ok=True)
        symlink_path = user_dir / "link_to_external"
        symlink_path.symlink_to(external_dir)

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }
        backend = LocalStorageBackend(config)

        # 尝试通过符号链接访问外部文件（解析后的路径会超出 root_path）
        malicious_path = symlink_path / "secret.txt"

        # 验证抛出 UploadError（路径穿越攻击）
        with pytest.raises(UploadError, match="路径穿越攻击"):
            backend._validate_path_security(malicious_path)

    @staticmethod
    def test_valid_path_within_root(tmp_path):
        """测试合法路径（在 root_path 内）."""
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }
        backend = LocalStorageBackend(config)

        # 合法路径（在 root_path 内）
        valid_path = storage_root / "user1" / "web_chat1" / "20260515_120000" / "file.txt"

        # 验证不抛出异常
        backend._validate_path_security(valid_path)


class TestLocalStorageBackendOperations:
    """测试文件上传、下载、删除操作."""

    @pytest.mark.asyncio
    async def test_upload_file(self, tmp_path):
        """测试文件上传."""
        # 创建 LocalStorageBackend
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }
        backend = LocalStorageBackend(config)

        # 创建本地文件
        local_file = tmp_path / "test_upload.txt"
        local_file.write_text("test content")

        # 上传文件
        uri = await backend.upload_file(str(local_file), "user1", "chat1", "web")

        # 验证 URI 格式
        assert uri.startswith("file://")
        assert "user1" in uri
        assert "web_chat1" in uri  # 注意：路径格式为 {channel_type}_{chat_id}

        # 验证文件已复制到 storage_root
        uploaded_file = Path(uri.replace("file://", ""))
        assert uploaded_file.exists()
        assert uploaded_file.read_text() == "test content"

    @pytest.mark.asyncio
    async def test_download_file(self, tmp_path):
        """测试文件下载."""
        # 创建 LocalStorageBackend
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }
        backend = LocalStorageBackend(config)

        # 创建上传文件（使用正确的路径结构）
        uploaded_file = storage_root / "files" / "user1" / "web_chat1" / "20260515_120000" / "test.txt"
        uploaded_file.parent.mkdir(parents=True, exist_ok=True)
        uploaded_file.write_text("uploaded content")

        # 下载文件
        local_download_path = tmp_path / "downloads" / "test.txt"
        local_download_path.parent.mkdir(parents=True, exist_ok=True)

        uri = f"file://{uploaded_file}"
        await backend.download_file(uri, str(local_download_path))

        # 验证文件已下载
        assert local_download_path.exists()
        assert local_download_path.read_text() == "uploaded content"

    @pytest.mark.asyncio
    async def test_delete_chat_files(self, tmp_path):
        """测试删除 chat 目录下的文件."""
        # 创建 LocalStorageBackend
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }
        backend = LocalStorageBackend(config)

        # 创建多个文件（使用正确的路径结构）
        chat_dir = storage_root / "files" / "user1" / "web_chat1" / "20260515_120000"
        chat_dir.mkdir(parents=True, exist_ok=True)

        (chat_dir / "file1.txt").write_text("content1")
        (chat_dir / "file2.txt").write_text("content2")

        # 删除 chat 目录下的所有文件
        deleted_count = await backend.delete_chat_files("user1", "chat1", "web")

        # 验证文件已删除
        assert deleted_count == 2
        assert not (chat_dir / "file1.txt").exists()
        assert not (chat_dir / "file2.txt").exists()

    @pytest.mark.asyncio
    async def test_delete_user_files(self, tmp_path):
        """测试删除用户过期文件."""
        # 创建 LocalStorageBackend
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }
        backend = LocalStorageBackend(config)

        # 创建用户目录和文件（使用正确的路径结构）
        user_dir = storage_root / "files" / "user1"
        user_dir.mkdir(parents=True, exist_ok=True)

        # 创建旧文件（模拟过期）
        old_chat_dir = user_dir / "web_old_chat" / "20260513_120000"
        old_chat_dir.mkdir(parents=True, exist_ok=True)
        old_file = old_chat_dir / "old_file.txt"
        old_file.write_text("old content")

        # 修改文件时间（模拟过期）
        old_time = time.time() - 48 * 3600  # 48小时前
        os.utime(old_file, (old_time, old_time))

        # 创建新文件（未过期）
        new_chat_dir = user_dir / "web_new_chat" / "20260515_120000"
        new_chat_dir.mkdir(parents=True, exist_ok=True)
        new_file = new_chat_dir / "new_file.txt"
        new_file.write_text("new content")

        # 删除过期文件（older_than_hours=24）
        deleted_count = await backend.delete_user_files("user1", older_than_hours=24)

        # 验证旧文件已删除，新文件保留
        assert deleted_count >= 1
        assert not old_file.exists()
        assert new_file.exists()

    @staticmethod
    def test_config_validation_default_root_path(tmp_path):
        """测试配置验证使用默认 root_path."""
        _ = tmp_path  # fixture required by pytest but not used in this test
        config = {
            "type": "local"
        }
        backend = LocalStorageBackend(config)

        # 验证使用默认 root_path
        assert backend.root_path == Path("/tmp/jiuwenclaw-storage").resolve()

    @staticmethod
    def test_config_validation_creates_root_path(tmp_path):
        """测试配置验证自动创建 root_path."""
        storage_root = tmp_path / "auto_created_storage"
        assert not storage_root.exists()

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }
        _ = LocalStorageBackend(config)  # backend creation has side effect: creates root_path

        # 验证 root_path 已创建
        assert storage_root.exists()
        assert storage_root.is_dir()