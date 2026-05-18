# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

# pylint: disable=protected-access
# pylint: disable=no-self-argument
# 测试代码访问私有成员和不需要实例访问的测试方法是合理的测试实践

"""测试 JiuWenClaw 文件处理优化 - 统一的文件流处理."""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.interface import (AgentRequest, AgentResponse,
                                              JiuWenClaw)
from jiuwenclaw.storage.backends.local import LocalStorageBackend


class TestExtractChatId:
    """测试 _extract_chat_id() 统一提取逻辑."""

    @staticmethod
    def test_extract_chat_id_web_channel():
        """测试 Web 渠道的 chat_id 提取."""
        request = AgentRequest(
            request_id="test_request_1",
            session_id="web_session123",
            channel_id="web",
            req_method="chat",
            params={"query": "test"}
        )

        cleaned_chat_id, channel_type = JiuWenClaw._extract_chat_id(request)

        assert channel_type == "web"
        # Web 渠道使用 session_id，并保留下划线和横线
        assert cleaned_chat_id == "web_session123"

    @staticmethod
    def test_extract_chat_id_dingtalk_channel():
        """测试钉钉渠道的 chat_id 提取."""
        request = AgentRequest(
            request_id="test_request_2",
            session_id="dingtalk_chat456",
            channel_id="dingtalk",
            req_method="chat",
            params={"query": "test"}
        )

        cleaned_chat_id, channel_type = JiuWenClaw._extract_chat_id(request)

        assert channel_type == "dingtalk"
        # 钉钉 chat_id 经过 sanitize_chat_id 清理（移除特殊字符）
        assert "chat456" in cleaned_chat_id

    @staticmethod
    def test_extract_chat_id_xiaoyi_channel():
        """测试小易渠道的 chat_id 提取."""
        request = AgentRequest(
            request_id="test_request_3",
            session_id="xiaoyi_conv789",
            channel_id="xiaoyi",
            req_method="chat",
            params={"query": "test"}
        )

        cleaned_chat_id, channel_type = JiuWenClaw._extract_chat_id(request)

        assert channel_type == "xiaoyi"
        assert "conv789" in cleaned_chat_id

    @staticmethod
    def test_extract_chat_id_wecom_channel():
        """测试企业微信渠道的 chat_id 提取."""
        request = AgentRequest(
            request_id="test_request_4",
            session_id="wecom_group123",
            channel_id="wecom",
            req_method="chat",
            params={"query": "test"}
        )

        cleaned_chat_id, channel_type = JiuWenClaw._extract_chat_id(request)

        assert channel_type == "wecom"
        # 企业微信 chat_id 经过 sanitize_chat_id 清理
        assert "group123" in cleaned_chat_id

    @staticmethod
    def test_extract_chat_id_unknown_channel():
        """测试未知渠道的处理."""
        request = AgentRequest(
            request_id="test_request_5",
            session_id="unknown_id",
            channel_id="unknown",
            req_method="chat",
            params={"query": "test"}
        )

        cleaned_chat_id, channel_type = JiuWenClaw._extract_chat_id(request)

        assert channel_type == "unknown"
        assert cleaned_chat_id  # 应有非空值

    @staticmethod
    def test_extract_chat_id_missing_channel():
        """测试缺失 channel_id 的处理."""
        request = AgentRequest(
            request_id="test_request_6",
            session_id="session_id",
            channel_id=None,
            req_method="chat",
            params={"query": "test"}
        )

        cleaned_chat_id, channel_type = JiuWenClaw._extract_chat_id(request)

        assert channel_type == "unknown"  # 默认值
        assert cleaned_chat_id


class TestBuildInputsFilesPassing:
    """测试 inputs["files"] 直接传递功能."""

    @staticmethod
    def test_build_inputs_passes_files_dict():
        """测试 inputs["files"] 传递字典格式文件."""
        # 直接测试方法，不需要实例
        request = AgentRequest(
            request_id="test_request_7",
            session_id="test_session",
            channel_id="web",
            req_method="chat",
            params={
                "query": "test query",
                "files": {
                    "file1": {"path": "/tmp/file1.txt", "name": "file1.txt"},
                    "file2": {"path": "/tmp/file2.txt", "name": "file2.txt"}
                }
            }
        )

        # 创建真实的 JiuWenClaw 实例来调用方法
        with patch('jiuwenclaw.config.get_config') as mock_config:
            mock_config.return_value = {"preferred_language": "zh"}

            jiuwenclaw = JiuWenClaw()
            inputs, memory_mode, raw_query = jiuwenclaw._build_inputs(request)

        # 验证 inputs["files"] 存在且内容正确
        assert "files" in inputs
        assert inputs["files"] == request.params["files"]

    @staticmethod
    def test_build_inputs_passes_files_list():
        """测试 inputs["files"] 传递列表格式文件."""
        request = AgentRequest(
            request_id="test_request_8",
            session_id="test_session",
            channel_id="web",
            req_method="chat",
            params={
                "query": "test query",
                "files": [
                    {"path": "/tmp/file1.txt", "name": "file1.txt"},
                    {"path": "/tmp/file2.txt", "name": "file2.txt"}
                ]
            }
        )

        with patch('jiuwenclaw.config.get_config') as mock_config:
            mock_config.return_value = {"preferred_language": "zh"}

            jiuwenclaw = JiuWenClaw()
            inputs, memory_mode, raw_query = jiuwenclaw._build_inputs(request)

        # 验证 inputs["files"] 存在且内容正确
        assert "files" in inputs
        assert inputs["files"] == request.params["files"]

    @staticmethod
    def test_build_inputs_empty_files():
        """测试 inputs["files"] 缺失时的处理."""
        request = AgentRequest(
            request_id="test_request_9",
            session_id="test_session",
            channel_id="web",
            req_method="chat",
            params={
                "query": "test query"
                # 没有 files 字段
            }
        )

        with patch('jiuwenclaw.config.get_config') as mock_config:
            mock_config.return_value = {"preferred_language": "zh"}

            jiuwenclaw = JiuWenClaw()
            inputs, memory_mode, raw_query = jiuwenclaw._build_inputs(request)

        # 验证 inputs["files"] 默认为空列表
        assert "files" in inputs
        assert inputs["files"] == []


class TestPayloadFilesUnifiedStructure:
    """测试 payload.files 统一结构处理."""

    @pytest.mark.asyncio
    async def test_upload_agent_files_standardizes_structure(self, tmp_path):
        """测试 upload_agent_files() 标准化 file_info 结构."""
        # 创建 LocalStorageBackend
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }

        mock_storage = LocalStorageBackend(config)

        # 创建本地文件
        local_file = tmp_path / "test_file.txt"
        local_file.write_text("test content")

        # 创建 response，payload.files 包含文件信息（缺少 name 字段）
        response = AgentResponse(
            request_id="test_request_10",
            channel_id="web",
            ok=True,
            payload={
                "files": [
                    {"path": str(local_file)}  # 只有 path，缺少 name
                ]
            }
        )

        # 创建 JiuWenClaw 实例并调用 upload_agent_files 方法
        with patch('jiuwenclaw.config.get_config') as mock_config:
            mock_config.return_value = {"preferred_language": "zh"}
            with patch.object(JiuWenClaw, '_get_storage', return_value=mock_storage):
                jiuwenclaw = JiuWenClaw()
                await jiuwenclaw.upload_agent_files(response, "user1", "chat1", "web")

        # 验证 file_info 已标准化（添加 name 和 uri）
        assert response.payload["files"][0]["name"] == "test_file.txt"
        assert "uri" in response.payload["files"][0]
        assert response.payload["files"][0]["uri"].startswith("file://")

    @pytest.mark.asyncio
    async def test_upload_agent_files_skips_nonexistent_files(self, tmp_path):
        """测试 upload_agent_files() 跳过不存在的文件并记录警告."""
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }

        mock_storage = LocalStorageBackend(config)

        # 创建 response，包含不存在的文件
        response = AgentResponse(
            request_id="test_request_11",
            channel_id="web",
            ok=True,
            payload={
                "files": [
                    {"path": "/nonexistent/file.txt"}  # 文件不存在
                ]
            }
        )

        # 创建 JiuWenClaw 实例并调用 upload_agent_files 方法
        with patch('jiuwenclaw.config.get_config') as mock_config:
            mock_config.return_value = {"preferred_language": "zh"}
            with patch.object(JiuWenClaw, '_get_storage', return_value=mock_storage):
                jiuwenclaw = JiuWenClaw()
                await jiuwenclaw.upload_agent_files(response, "user1", "chat1", "web")

        # 验证文件被跳过（payload 保持不变，uri 未添加）
        assert "uri" not in response.payload["files"][0]
        assert response.payload["files"][0]["path"] == "/nonexistent/file.txt"

    @pytest.mark.asyncio
    async def test_upload_agent_files_only_processes_files_field(self, tmp_path):
        """测试 upload_agent_files() 只处理 payload.files 字段."""
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }

        mock_storage = LocalStorageBackend(config)

        # 创建本地文件
        local_file = tmp_path / "test_file.txt"
        local_file.write_text("test content")

        # 创建 response，包含 output_files 字段（旧格式）
        response = AgentResponse(
            request_id="test_request_12",
            channel_id="web",
            ok=True,
            payload={
                "output_files": [
                    {"path": str(local_file)}
                ],
                "files": []  # files 字段为空
            }
        )

        # 创建 JiuWenClaw 实例并调用 upload_agent_files 方法
        with patch('jiuwenclaw.config.get_config') as mock_config:
            mock_config.return_value = {"preferred_language": "zh"}
            with patch.object(JiuWenClaw, '_get_storage', return_value=mock_storage):
                jiuwenclaw = JiuWenClaw()
                await jiuwenclaw.upload_agent_files(response, "user1", "chat1", "web")

        # 验证只处理 files 字段（output_files 未处理）
        assert response.payload["output_files"][0].get("uri") is None
        assert len(response.payload["files"]) == 0


class TestPrepareFilesForAgent:
    """测试 prepare_files_for_agent() 统一文件准备."""

    @pytest.mark.asyncio
    async def test_prepare_files_dict_format(self, tmp_path):
        """测试 prepare_files_for_agent() 处理字典格式文件."""
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        # 创建上传文件
        uploaded_file = storage_root / "files" / "user1" / "web_chat1" / "20260515_120000" / "upload.txt"
        uploaded_file.parent.mkdir(parents=True, exist_ok=True)
        uploaded_file.write_text("uploaded content")

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }

        with patch('jiuwenclaw.agentserver.interface.JiuWenClaw._get_storage') as mock_get_storage:
            mock_storage = LocalStorageBackend(config)
            mock_get_storage.return_value = mock_storage

            # 创建 JiuWenClaw
            agent_server = JiuWenClaw()
            agent_server._get_storage = mock_get_storage

            # 创建请求（字典格式）
            request = AgentRequest(
                request_id="test_request_13",
                session_id="web_chat1",
                channel_id="web",
                req_method="chat",
                params={
                    "query": "test",
                    "files": {
                        "file1": {
                            "uri": f"file://{uploaded_file}",
                            "name": "upload.txt"
                        }
                    }
                }
            )

            # 调用 prepare_files_for_agent
            await agent_server.prepare_files_for_agent(request)

            # 验证文件已下载到工作目录
            # （实际验证需要检查 request.params["files"] 被更新）

    @pytest.mark.asyncio
    async def test_prepare_files_list_format(self, tmp_path):
        """测试 prepare_files_for_agent() 处理列表格式文件."""
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        # 创建上传文件
        uploaded_file = storage_root / "files" / "user1" / "web_chat1" / "20260515_120000" / "upload.txt"
        uploaded_file.parent.mkdir(parents=True, exist_ok=True)
        uploaded_file.write_text("uploaded content")

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }

        with patch('jiuwenclaw.agentserver.interface.JiuWenClaw._get_storage') as mock_get_storage:
            mock_storage = LocalStorageBackend(config)
            mock_get_storage.return_value = mock_storage

            # 创建 JiuWenClaw
            agent_server = JiuWenClaw()
            agent_server._get_storage = mock_get_storage

            # 创建请求（列表格式）
            request = AgentRequest(
                request_id="test_request_14",
                session_id="web_chat1",
                channel_id="web",
                req_method="chat",
                params={
                    "query": "test",
                    "files": [
                        {
                            "uri": f"file://{uploaded_file}",
                            "name": "upload.txt"
                        }
                    ]
                }
            )

            # 调用 prepare_files_for_agent
            await agent_server.prepare_files_for_agent(request)

            # 验证文件已下载到工作目录
            # （实际验证需要检查 request.params["files"] 被更新）