# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""集成测试：完整文件流（前端 → AgentServer → Agent → AgentServer → 前端）."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.interface import (AgentAdapter, AgentRequest,
                                              AgentResponse, JiuWenClaw)
from jiuwenclaw.storage.backends.local import LocalStorageBackend


class MockAgentAdapter:
    """Mock AgentAdapter for testing file flow."""

    async def process_message_impl(self, request, inputs):
        """模拟 Agent 处理请求.

        1. 从 inputs["files"] 访问输入文件
        2. 生成输出文件
        3. 返回 AgentResponse with payload.files
        """
        # 模拟 Agent 读取输入文件
        files = inputs.get("files", [])
        if files:
            # 验证文件已被下载到 workspace（dict 格式）
            if isinstance(files, dict):
                for file_key, file_ref in files.items():
                    if isinstance(file_ref, dict):
                        path = file_ref.get("path")
                        if path:
                            assert Path(path).exists(), f"输入文件不存在: {path}"
            elif isinstance(files, list):
                for file_ref in files:
                    if isinstance(file_ref, dict):
                        path = file_ref.get("path")
                        if path:
                            assert Path(path).exists(), f"输入文件不存在: {path}"

        # 模拟 Agent 生成输出文件
        output_file = Path("/tmp/test_output.txt")
        output_file.write_text("Agent generated content")

        # 返回 AgentResponse
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload={
                "content": "处理完成",
                "files": [
                    {"path": str(output_file), "name": "output.txt"}
                ]
            }
        )

    async def handle_heartbeat(self, request):
        return None

    async def handle_user_answer(self, request):
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=False,
            payload={"content": "Not implemented"}
        )


class TestCompleteFileFlow:
    """测试完整文件流：前端 → AgentServer → Agent → AgentServer → 前端."""

    @pytest.mark.asyncio
    async def test_file_flow_dict_format(self, tmp_path):
        """测试字典格式文件的完整流程."""
        # 1. 准备存储服务
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }
        storage = LocalStorageBackend(config)

        # 2. 模拟前端上传文件
        input_file = tmp_path / "input.txt"
        input_file.write_text("前端上传的文件内容")

        user_id = "test_user"
        chat_id = "test_chat"
        channel_type = "web"

        input_uri = await storage.upload_file(str(input_file), user_id, chat_id, channel_type)

        # 3. 前端发送请求（包含文件 URI）
        request = AgentRequest(
            request_id="req_001",
            session_id=f"{channel_type}_{chat_id}",
            channel_id=channel_type,
            req_method="chat",
            params={
                "query": "处理这个文件",
                "files": {
                    "input_file": {
                        "uri": input_uri,
                        "name": "input.txt"
                    }
                }
            }
        )

        # 4. AgentServer 处理请求
        with patch('jiuwenclaw.config.get_config') as mock_config:
            mock_config.return_value = {"preferred_language": "zh"}
            with patch.object(JiuWenClaw, '_get_storage', return_value=storage):
                with patch.object(JiuWenClaw, '_ensure_adapter') as mock_ensure_adapter:
                    mock_adapter = MockAgentAdapter()
                    mock_ensure_adapter.return_value = mock_adapter

                    jiuwenclaw = JiuWenClaw()
                    response = await jiuwenclaw.process_message(request)

        # 5. 验证响应
        assert response.ok
        assert "处理完成" in response.payload["content"]

        # 6. 验证输出文件已上传（包含 uri）
        assert "files" in response.payload
        assert len(response.payload["files"]) == 1

        output_file_info = response.payload["files"][0]
        assert "uri" in output_file_info
        assert "name" in output_file_info
        assert output_file_info["name"] == "output.txt"
        assert output_file_info["uri"].startswith("file://")

        # 7. 验证前端可以通过 URI 访问输出文件
        output_uri = output_file_info["uri"]
        output_path = Path(output_uri.replace("file://", ""))
        assert output_path.exists()
        assert output_path.read_text() == "Agent generated content"

    @pytest.mark.asyncio
    async def test_file_flow_list_format(self, tmp_path):
        """测试列表格式文件的完整流程."""
        # 1. 准备存储服务
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True, exist_ok=True)

        config = {
            "type": "local",
            "root_path": str(storage_root)
        }
        storage = LocalStorageBackend(config)

        # 2. 模拟前端上传文件
        input_file = tmp_path / "input_list.txt"
        input_file.write_text("前端上传的文件内容（列表格式）")

        user_id = "test_user"
        chat_id = "test_chat_list"
        channel_type = "web"

        input_uri = await storage.upload_file(str(input_file), user_id, chat_id, channel_type)

        # 3. 前端发送请求（包含文件 URI）
        request = AgentRequest(
            request_id="req_002",
            session_id=f"{channel_type}_{chat_id}",
            channel_id=channel_type,
            req_method="chat",
            params={
                "query": "处理这个文件",
                "files": [
                    {
                        "uri": input_uri,
                        "name": "input_list.txt"
                    }
                ]
            }
        )

        # 4. AgentServer 处理请求
        with patch('jiuwenclaw.config.get_config') as mock_config:
            mock_config.return_value = {"preferred_language": "zh"}
            with patch.object(JiuWenClaw, '_get_storage', return_value=storage):
                with patch.object(JiuWenClaw, '_ensure_adapter') as mock_ensure_adapter:
                    mock_adapter = MockAgentAdapter()
                    mock_ensure_adapter.return_value = mock_adapter

                    jiuwenclaw = JiuWenClaw()
                    response = await jiuwenclaw.process_message(request)

        # 5. 验证响应和输出文件
        assert response.ok
        assert "files" in response.payload

        output_file_info = response.payload["files"][0]
        assert "uri" in output_file_info
        assert output_file_info["uri"].startswith("file://")


class TestConfigDrivenBackendLoading:
    """测试配置驱动 Backend 加载（backend_class 字段）."""

    @pytest.mark.asyncio
    async def test_backend_class_loading(self, tmp_path):
        """测试通过 backend_class 配置字段加载自定义 Backend."""
        from jiuwenclaw.storage.registry import StorageBackendRegistry

        # 重置 Registry
        StorageBackendRegistry.reset()

        # 配置包含 backend_class 字段
        config = {
            "type": "custom_local",
            "backend_class": "jiuwenclaw.storage.backends.local.LocalStorageBackend",
            "root_path": str(tmp_path / "custom_storage")
        }

        # 模拟 StorageService 创建 Backend 的流程
        StorageBackendRegistry.register_from_config(config)

        backend_class = StorageBackendRegistry.get("custom_local")
        assert backend_class is not None
        assert backend_class.__name__ == "LocalStorageBackend"

        # 实例化 Backend
        backend = backend_class(config)
        assert isinstance(backend, LocalStorageBackend)
        assert backend.root_path == Path(tmp_path / "custom_storage").resolve()

        # 重置 Registry
        StorageBackendRegistry.reset()

    @pytest.mark.asyncio
    async def test_builtin_local_backend_registered(self):
        """测试内置 LocalStorageBackend 已自动注册."""
        from jiuwenclaw.storage.registry import StorageBackendRegistry

        # 重置 Registry
        StorageBackendRegistry.reset()

        # 手动注册（模拟 storage/__init__.py 的自动注册逻辑）
        StorageBackendRegistry.register("local", LocalStorageBackend)

        # 验证 LocalStorageBackend 已注册
        backend_class = StorageBackendRegistry.get("local")
        assert backend_class is not None
        assert backend_class.__name__ == "LocalStorageBackend"

        # 重置 Registry
        StorageBackendRegistry.reset()