# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""存储服务集成测试。

测试完整的文件上传下载流程，包括：
1. 从对象存储下载文件到本地 workspace
2. 上传本地文件到对象存储
3. 与 AgentServer 的集成
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.interface import JiuWenClaw
from jiuwenclaw.schema.agent import AgentRequest, ReqMethod


@pytest.fixture
def temp_workspace():
    """创建临时工作空间目录."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        workspace = Path(tmp_dir) / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        yield workspace


@pytest.fixture
def temp_storage_dir():
    """创建临时存储目录."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = Path(tmp_dir) / "storage"
        storage.mkdir(parents=True, exist_ok=True)
        yield storage


@pytest.mark.asyncio
async def test_storage_service_initialization(temp_workspace):
    """测试存储服务初始化."""
    # 创建临时配置
    with patch('jiuwenclaw.config.get_config') as mock_config:
        mock_config.return_value = {
            "storage": {
                "type": "local",
                "local": {
                    "base_dir": str(temp_workspace / "storage"),
                }
            }
        }

        from jiuwenclaw.storage import StorageService

        # 重置单例
        StorageService.reset_instance()

        # 获取实例
        backend = await StorageService.get_instance()

        assert backend is not None
        assert hasattr(backend, 'download_file')
        assert hasattr(backend, 'upload_file')


@pytest.mark.asyncio
async def test_local_storage_download_file(temp_storage_dir, temp_workspace):
    """测试从本地文件系统下载文件."""
    from jiuwenclaw.storage.local_backend import LocalStorageBackend

    # 创建测试文件
    test_file = temp_storage_dir / "test.txt"
    test_file.write_text("Hello, World!")

    # 创建 LocalStorageBackend
    backend = LocalStorageBackend({"base_dir": str(temp_storage_dir)})

    # 下载文件
    local_path = str(temp_workspace / "downloaded.txt")
    await backend.download_file(f"file://{test_file}", local_path)

    # 验证文件已下载
    assert Path(local_path).exists()
    assert Path(local_path).read_text() == "Hello, World!"


@pytest.mark.asyncio
async def test_local_storage_upload_file(temp_workspace):
    """测试上传文件到本地存储."""
    from jiuwenclaw.storage.local_backend import LocalStorageBackend

    # 创建测试文件
    test_file = temp_workspace / "test.txt"
    test_file.write_text("Hello, World!")

    # 创建 LocalStorageBackend
    backend = LocalStorageBackend({"base_dir": str(temp_workspace / "storage")})

    # 上传文件
    uri = await backend.upload_file(str(test_file), "test_user")

    # 验证返回的 URI
    assert uri.startswith("file://")
    assert "test.txt" in uri


@pytest.mark.asyncio
async def test_agentserver_prepare_files(temp_workspace):
    """测试 AgentServer 文件预处理功能."""
    # 创建 JiuWenClaw 实例
    with patch('jiuwenclaw.config.get_config') as mock_config:
        mock_config.return_value = {
            "storage": {
                "type": "local",
                "local": {
                    "base_dir": str(temp_workspace / "storage"),
                }
            }
        }

        agent = JiuWenClaw(user_workspace_dir=str(temp_workspace))

        # 创建测试文件
        test_file = temp_workspace / "test_source.txt"
        test_file.write_text("Test content")

        # 创建请求
        request = AgentRequest(
            request_id="test_request",
            channel_id="test_channel",
            session_id="test_session",
            req_method=ReqMethod.CHAT_SEND,
            metadata={"user_id": "test_user"},
            params={
                "query": "test query",
                "files": {
                    "file1": {
                        "uri": f"file://{test_file}",
                        "name": "test_source.txt"
                    }
                }
            }
        )

        # 预处理文件
        await agent.prepare_files_for_agent(request)

        # 验证文件已下载
        files = request.params.get("files", {})
        assert "file1" in files
        assert "path" in files["file1"]

        downloaded_path = files["file1"]["path"]
        assert Path(downloaded_path).exists()
        assert Path(downloaded_path).read_text() == "Test content"


@pytest.mark.asyncio
async def test_agentserver_upload_files(temp_workspace):
    """测试 AgentServer 文件后处理功能."""
    # 创建 JiuWenClaw 实例
    with patch('jiuwenclaw.config.get_config') as mock_config:
        mock_config.return_value = {
            "storage": {
                "type": "local",
                "local": {
                    "base_dir": str(temp_workspace / "storage"),
                }
            }
        }

        agent = JiuWenClaw(user_workspace_dir=str(temp_workspace))

        # 创建测试文件
        test_file = temp_workspace / "output.txt"
        test_file.write_text("Output content")

        # 创建响应
        from jiuwenclaw.schema.agent import AgentResponse
        response = AgentResponse(
            request_id="test_request",
            channel_id="test_channel",
            ok=True,
            payload={
                "content": "Test response",
                "files": [
                    {
                        "path": str(test_file),
                        "name": "output.txt"
                    }
                ]
            }
        )

        # 后处理文件
        await agent.upload_agent_files(response, "test_user")

        # 验证文件已上传（添加了 uri 字段）
        files = response.payload.get("files", [])
        assert len(files) > 0
        assert "uri" in files[0]
        assert files[0]["uri"].startswith("file://")
