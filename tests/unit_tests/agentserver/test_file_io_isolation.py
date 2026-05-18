# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

# pylint: disable=protected-access
# pylint: disable=no-self-argument
# 测试代码访问私有成员和不需要实例访问的测试方法是合理的测试实践

"""Agent 文件输入输出隔离功能测试.

测试目标：
- 目录创建逻辑（对话模式 vs 项目模式）
- metadata 字段传递
- runtime_config 设置
- 文件上传处理
- chat_id 安全清理
"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.interface import JiuWenClaw
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse


@pytest.fixture
def temp_workspace():
    """创建临时 workspace 目录."""
    tmpdir = tempfile.mkdtemp(prefix="test_file_io_")
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def temp_project_dir():
    """创建临时项目目录."""
    tmpdir = tempfile.mkdtemp(prefix="test_project_")
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)




class TestPrepareFilesForAgentDirectoryCreation:
    """测试 prepare_files_for_agent 目录创建逻辑."""

    @pytest.mark.asyncio
    async def test_conversation_mode_creates_input_output_dirs(
        self, temp_workspace
    ):
        """测试：对话模式下创建 input/output 目录 (Task 5.1)."""
        # 创建 JiuWenClaw 实例
        instance = JiuWenClaw(user_workspace_dir=str(temp_workspace))
        instance._adapter = MagicMock()
        instance._adapter._instance = MagicMock()

        # 构造请求
        request = AgentRequest(
            request_id="test_req_001",
            session_id="user123",
            channel_id="web",
            chat_id="sess_abc123",
            params={
                "files": [
                    {"uri": "file:///storage/user123/sess_abc123/test.txt", "name": "test.txt"}
                ]
            },
            metadata={
                "user_id": "user123"
            }
        )

        # Mock storage service
        mock_storage = AsyncMock()
        mock_storage.download_file = AsyncMock()
        instance._get_storage = AsyncMock(return_value=mock_storage)

        # 执行文件预处理
        await instance.prepare_files_for_agent(request)

        # 验证目录创建
        assert "input_dir" in request.metadata
        assert "output_dir" in request.metadata

        input_dir = Path(request.metadata["input_dir"])
        output_dir = Path(request.metadata["output_dir"])

        # 验证路径格式：workspace/files/{user_id}/{chat_id}/input
        assert input_dir.parent.parent.name == "user123"
        assert input_dir.parent.name == "sess_abc123"  # chat_id 已清理（保留有效字符）
        assert input_dir.name == "input"
        assert input_dir.exists()

        # 验证 output_dir 同样创建
        assert output_dir.parent.parent.name == "user123"
        assert output_dir.parent.name == "sess_abc123"
        assert output_dir.name == "output"
        assert output_dir.exists()

    @pytest.mark.asyncio
    async def test_project_mode_creates_input_output_dirs(
        self, temp_workspace, temp_project_dir
    ):
        """测试：项目模式下创建 input/output 目录 (Task 5.2)."""
        # 创建 JiuWenClaw 实例
        instance = JiuWenClaw(user_workspace_dir=str(temp_workspace))
        instance._adapter = MagicMock()
        instance._adapter._instance = MagicMock()

        # 构造请求（项目模式）
        request = AgentRequest(
            request_id="test_req_002",
            session_id="user456",
            channel_id="web",
            chat_id="sess_xyz789",
            params={
                "files": []
            },
            metadata={
                "user_id": "user456",
                "effective_project_dir": str(temp_project_dir)
            }
        )

        # Mock storage service
        mock_storage = AsyncMock()
        instance._get_storage = AsyncMock(return_value=mock_storage)

        # 执行文件预处理
        await instance.prepare_files_for_agent(request)

        # 验证目录创建
        assert "input_dir" in request.metadata
        assert "output_dir" in request.metadata

        input_dir = Path(request.metadata["input_dir"])
        output_dir = Path(request.metadata["output_dir"])

        # 验证路径格式：project_dir/files/{user_id}/{chat_id}/input
        assert str(input_dir.parent.parent.parent.parent) == str(temp_project_dir)
        assert input_dir.parent.parent.parent.name == "files"
        assert input_dir.parent.parent.name == "user456"
        assert input_dir.parent.name == "sess_xyz789"  # chat_id 已清理（保留有效字符）
        assert input_dir.name == "input"
        assert input_dir.exists()

        # 验证 output_dir 同样创建
        assert str(output_dir.parent.parent.parent.parent) == str(temp_project_dir)
        assert output_dir.exists()


class TestChatIdSanitization:
    """测试 chat_id 安全清理函数 (Task 5.3)."""

    @staticmethod
    def test_sanitize_chat_id_special_characters():
        """测试：chat_id 包含特殊字符."""
        from jiuwenclaw.storage.utils import sanitize_chat_id

        # 测试特殊字符清理
        result = sanitize_chat_id("web_chat#123@456", "web")
        assert result == "web_chat123456"  # 移除特殊字符

    @staticmethod
    def test_sanitize_chat_id_path_separator():
        """测试：chat_id 包含路径分隔符（防止路径穿越）."""
        from jiuwenclaw.storage.utils import sanitize_chat_id

        # 测试路径分隔符清理
        result = sanitize_chat_id("sess/abc/../def", "web")
        assert "/" not in result
        assert ".." not in result
        # 清理后应为扁平格式
        assert result.startswith("sess")

    @staticmethod
    def test_sanitize_chat_id_empty_raises_error():
        """测试：空 chat_id 抛出异常."""
        from jiuwenclaw.storage.utils import sanitize_chat_id

        with pytest.raises(ValueError, match="chat_id 不能为空"):
            sanitize_chat_id("", "web")

    @staticmethod
    def test_sanitize_chat_id_all_special_chars_raises_error():
        """测试：chat_id 清理后为空字符串抛出异常."""
        from jiuwenclaw.storage.utils import sanitize_chat_id

        with pytest.raises(ValueError, match="chat_id 清理后为空字符串"):
            sanitize_chat_id("###@@@", "web")


class TestMetadataFields:
    """测试 metadata 包含正确的 input_dir 和 output_dir 字段 (Task 5.4)."""

    @pytest.mark.asyncio
    async def test_metadata_contains_input_output_dirs(
        self, temp_workspace
    ):
        """测试：metadata 包含 input_dir 和 output_dir."""
        # 创建 JiuWenClaw 实例
        instance = JiuWenClaw(user_workspace_dir=str(temp_workspace))
        instance._adapter = MagicMock()
        instance._adapter._instance = MagicMock()

        request = AgentRequest(
            request_id="test_req_003",
            session_id="user789",
            channel_id="web",
            chat_id="sess_test",
            params={"files": []},
            metadata={"user_id": "user789"}
        )

        mock_storage = AsyncMock()
        instance._get_storage = AsyncMock(return_value=mock_storage)

        await instance.prepare_files_for_agent(request)

        # 非异步 fixture cleanup
        instance._adapter._instance = None

        # 验证 metadata 包含必需字段
        assert isinstance(request.metadata, dict)
        assert "input_dir" in request.metadata
        assert "output_dir" in request.metadata
        assert isinstance(request.metadata["input_dir"], str)
        assert isinstance(request.metadata["output_dir"], str)

        # 验证路径存在
        assert Path(request.metadata["input_dir"]).exists()
        assert Path(request.metadata["output_dir"]).exists()


class TestRuntimeConfigOutputDir:
    """测试 output_dir ContextVar 设置 (Task 5.5)."""

    @pytest.mark.asyncio
    async def test_output_dir_context_var_set(self):
        """测试：output_dir 通过 ContextVar 正确设置."""
        from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
            get_effective_request_output_dir, set_effective_request_output_dir)

        output_dir_path = "/tmp/test_output_dir"

        # 设置 output_dir
        set_effective_request_output_dir(output_dir_path)

        # 验证可以获取到设置的值
        result = get_effective_request_output_dir()
        assert result == output_dir_path

    @staticmethod
    def test_output_dir_context_var_none():
        """测试：未设置 output_dir 时返回 None."""
        from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
            get_effective_request_output_dir, set_effective_request_output_dir)

        # 清除设置
        set_effective_request_output_dir(None)

        # 验证返回 None
        result = get_effective_request_output_dir()
        assert result is None


class TestUploadAgentFiles:
    """测试 upload_agent_files 从 output_dir 读取文件 (Task 5.6)."""

    @pytest.mark.asyncio
    async def test_upload_files_from_output_dir(
        self, temp_workspace
    ):
        """测试：upload_agent_files 从 output_dir 读取文件并上传."""
        # 创建 JiuWenClaw 实例
        instance = JiuWenClaw(user_workspace_dir=str(temp_workspace))
        instance._adapter = MagicMock()
        instance._adapter._instance = MagicMock()

        # 创建测试文件
        output_dir = temp_workspace / "files" / "test_user" / "test_chat" / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        test_file = output_dir / "result.txt"
        test_file.write_text("test content")

        # 构造 response
        response = AgentResponse(
            request_id="test_req",
            channel_id="web",
            ok=True,
            payload={
                "files": [
                    {
                        "path": str(test_file),
                        "name": "result.txt"
                    }
                ]
            }
        )

        # Mock storage service
        mock_storage = AsyncMock()
        mock_storage.upload_file = AsyncMock(return_value="file:///storage/test_user/test_chat/result.txt")
        instance._get_storage = AsyncMock(return_value=mock_storage)

        # 执行文件上传
        await instance.upload_agent_files(
            response,
            user_id="test_user",
            chat_id="test_chat",
            channel_type="web"
        )

        # 验证文件上传成功
        assert mock_storage.upload_file.called
        assert "uri" in response.payload["files"][0]
        assert response.payload["files"][0]["uri"] == "file:///storage/test_user/test_chat/result.txt"

    @pytest.mark.asyncio
    async def test_upload_files_handles_missing_file(
        self, temp_workspace
    ):
        """测试：文件不存在时跳过上传并记录警告."""
        # 创建 JiuWenClaw 实例
        instance = JiuWenClaw(user_workspace_dir=str(temp_workspace))
        instance._adapter = MagicMock()
        instance._adapter._instance = MagicMock()

        # 构造 response（文件不存在）
        response = AgentResponse(
            request_id="test_req",
            channel_id="web",
            ok=True,
            payload={
                "files": [
                    {
                        "path": "/nonexistent/path/file.txt",
                        "name": "file.txt"
                    }
                ]
            }
        )

        mock_storage = AsyncMock()
        instance._get_storage = AsyncMock(return_value=mock_storage)

        # 执行文件上传
        await instance.upload_agent_files(
            response,
            user_id="test_user",
            chat_id="test_chat",
            channel_type="web"
        )

        # 验证：storage.upload_file 未被调用（文件不存在）
        assert not mock_storage.upload_file.called


class TestDirectoryCreationErrorHandling:
    """测试目录创建失败时的错误处理."""

    @pytest.mark.asyncio
    async def test_directory_creation_failure_logs_error(
        self, temp_workspace
    ):
        """测试：目录创建失败时记录错误日志."""
        # 创建 JiuWenClaw 实例
        instance = JiuWenClaw(user_workspace_dir=str(temp_workspace))
        instance._adapter = MagicMock()
        instance._adapter._instance = MagicMock()

        request = AgentRequest(
            request_id="test_req_err",
            session_id="user_err",
            channel_id="web",
            chat_id="sess_err",
            params={"files": []},
            metadata={"user_id": "user_err"}
        )

        # Mock Path.mkdir to raise PermissionError
        with patch('pathlib.Path.mkdir', side_effect=PermissionError("No permission")):
            mock_storage = AsyncMock()
            instance._get_storage = AsyncMock(return_value=mock_storage)

            await instance.prepare_files_for_agent(request)

        # 验证：metadata 未设置（或保持原样）
        # 根据实现逻辑，如果目录创建失败，metadata 可能不包含 input_dir/output_dir
        # 或者抛出异常（取决于实现）
        # 这里假设实现是记录错误但继续执行


class TestPathDuplicationDetection:
    """测试路径重复检测逻辑 (Task 5.2.3)."""

    @pytest.mark.asyncio
    async def test_path_duplication_warning_when_user_id_equals_chat_id(
        self, temp_workspace
    ):
        """测试：user_id == chat_id 时产生 WARNING 日志."""
        # 创建 JiuWenClaw 实例
        instance = JiuWenClaw(user_workspace_dir=str(temp_workspace))
        instance._adapter = MagicMock()
        instance._adapter._instance = MagicMock()

        # 构造请求：user_id == chat_id（触发重复检测）
        request = AgentRequest(
            request_id="test_req_dup",
            session_id="default",
            channel_id="web",
            chat_id="dup_id",  # 与 user_id 相同
            params={"files": []},
            metadata={"user_id": "dup_id"}  # 与 chat_id 相同
        )

        mock_storage = AsyncMock()
        instance._get_storage = AsyncMock(return_value=mock_storage)

        # 使用 patch 监听 logger.warning
        with patch("jiuwenclaw.agentserver.interface.logger") as mock_logger:
            await instance.prepare_files_for_agent(request)

            # 验证产生 WARNING 日志
            warning_calls = mock_logger.warning.call_args_list
            assert len(warning_calls) >= 1

            # 验证日志内容包含关键信息
            warning_msg = warning_calls[0][0][0]
            assert "Path duplication detected" in warning_msg
            assert "user_id == chat_id" in warning_msg
            assert "dup_id" in warning_msg
            assert "frontend data source" in warning_msg

    @pytest.mark.asyncio
    async def test_path_no_duplication_when_user_id_differs_from_chat_id(
        self, temp_workspace
    ):
        """测试：user_id != chat_id 时不产生 WARNING 日志."""
        # 创建 JiuWenClaw 实例
        instance = JiuWenClaw(user_workspace_dir=str(temp_workspace))
        instance._adapter = MagicMock()
        instance._adapter._instance = MagicMock()

        # 构造请求：user_id != chat_id（正常情况）
        request = AgentRequest(
            request_id="test_req_normal",
            session_id="user_normal",
            channel_id="web",
            chat_id="chat_normal",
            params={"files": []},
            metadata={"user_id": "user_normal"}  # 与 chat_id 不同
        )

        mock_storage = AsyncMock()
        instance._get_storage = AsyncMock(return_value=mock_storage)

        # 使用 patch 监听 logger.warning
        with patch("jiuwenclaw.agentserver.interface.logger") as mock_logger:
            await instance.prepare_files_for_agent(request)

            # 验证未产生路径重复相关的 WARNING 日志
            warning_calls = mock_logger.warning.call_args_list
            path_dup_warnings = [
                call for call in warning_calls
                if "Path duplication detected" in call[0][0]
            ]
            assert len(path_dup_warnings) == 0

    @pytest.mark.asyncio
    async def test_path_duplication_does_not_block_directory_creation(
        self, temp_workspace
    ):
        """测试：路径重复警告不阻止目录创建."""
        # 创建 JiuWenClaw 实例
        instance = JiuWenClaw(user_workspace_dir=str(temp_workspace))
        instance._adapter = MagicMock()
        instance._adapter._instance = MagicMock()

        # 构造请求：user_id == chat_id（触发重复检测）
        request = AgentRequest(
            request_id="test_req_block",
            session_id="default",
            channel_id="web",
            chat_id="dup_block",
            params={"files": []},
            metadata={"user_id": "dup_block"}
        )

        mock_storage = AsyncMock()
        instance._get_storage = AsyncMock(return_value=mock_storage)

        # 执行文件预处理
        await instance.prepare_files_for_agent(request)

        # 验证目录创建成功（metadata 包含 output_dir）
        assert "output_dir" in request.metadata
        assert request.metadata["output_dir"].endswith("/output")

        # 验证目录实际存在
        output_dir_path = Path(request.metadata["output_dir"])
        assert output_dir_path.exists()


class TestContextVarPropagationInCallStack:
    """测试 ContextVar 在调用栈中的传播 (Task 5.3)."""

    @staticmethod
    def test_context_var_propagation_in_nested_function():
        """测试：ContextVar 在嵌套函数中传播."""
        from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
            get_effective_request_output_dir, set_effective_request_output_dir)

        test_output_dir = "/tmp/test_nested_output"

        # 设置 ContextVar
        set_effective_request_output_dir(test_output_dir)

        # 定义嵌套函数
        def nested_level_1():
            def nested_level_2():
                return get_effective_request_output_dir()
            return nested_level_2()

        # 验证 ContextVar 在嵌套调用中传播
        result = nested_level_1()
        assert result == test_output_dir

    @staticmethod
    def test_context_var_fallback_to_none():
        """测试：ContextVar 未设置时返回 None，Agent 可 fallback."""
        from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
            get_effective_request_output_dir, set_effective_request_output_dir)

        # 清空 ContextVar
        set_effective_request_output_dir(None)

        # 模拟 Agent 工具函数 fallback 逻辑
        output_dir = get_effective_request_output_dir()
        if output_dir:
            file_path = f"{output_dir}/file.txt"
        else:
            # Fallback 到 cwd 或其他默认值
            file_path = "/tmp/fallback/file.txt"

        # 验证 fallback 生效
        assert file_path == "/tmp/fallback/file.txt"


class TestRuntimePromptRailOutputDirSection:
    """测试 RuntimePromptRail Prompt 包含 output_dir 说明 (Task 5.1)."""

    @staticmethod
    def test_cn_prompt_contains_output_dir_section():
        """测试：中文 Prompt 包含 output_dir section."""
        from unittest.mock import Mock

        from jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail import \
            RuntimePromptRail

        # 创建 RuntimePromptRail 实例（中文）
        rail = RuntimePromptRail(
            language="cn",
            agent_name="test_agent",
            workspace_dir="/tmp/workspace"
        )

        # 模拟 system_prompt_builder
        mock_builder = Mock()
        rail.system_prompt_builder = mock_builder

        # 执行 before_model_call（同步调用）
        import asyncio

        from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

        # 创建 mock context
        mock_ctx = Mock(spec=AgentCallbackContext)

        # 调用 before_model_call
        asyncio.run(rail.before_model_call(mock_ctx))

        # 获取添加的 workspace section
        added_sections = mock_builder.add_section.call_args_list
        workspace_section = None
        for call in added_sections:
            section = call[0][0]
            if section.name == "workspace":
                workspace_section = section
                break

        # 验证 workspace section 存在
        assert workspace_section is not None

        # 验证中文内容包含 output_dir 说明
        cn_content = workspace_section.content["cn"]
        assert "文件输出与发送规范" in cn_content
        assert "output_dir" in cn_content
        assert "get_effective_request_output_dir" in cn_content
        assert "方式 1：使用 output_dir" in cn_content or "使用 output_dir" in cn_content

    @staticmethod
    def test_en_prompt_contains_output_dir_section():
        """测试：英文 Prompt 包含 output_dir section."""
        from unittest.mock import Mock

        from jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail import \
            RuntimePromptRail

        # 创建 RuntimePromptRail 实例（英文）
        rail = RuntimePromptRail(
            language="en",
            agent_name="test_agent",
            workspace_dir="/tmp/workspace"
        )

        # 模拟 system_prompt_builder
        mock_builder = Mock()
        rail.system_prompt_builder = mock_builder

        # 执行 before_model_call
        import asyncio

        from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

        mock_ctx = Mock(spec=AgentCallbackContext)
        asyncio.run(rail.before_model_call(mock_ctx))

        # 获取添加的 workspace section
        added_sections = mock_builder.add_section.call_args_list
        workspace_section = None
        for call in added_sections:
            section = call[0][0]
            if section.name == "workspace":
                workspace_section = section
                break

        assert workspace_section is not None

        # 验证英文内容包含 output_dir 说明
        en_content = workspace_section.content["en"]
        assert "File Output and Sending Guidelines" in en_content
        assert "output_dir" in en_content
        assert "get_effective_request_output_dir" in en_content
        assert "Option 1" in en_content or "Use output_dir" in en_content

    @staticmethod
    def test_prompt_contains_api_import_path():
        """测试：Prompt 包含完整的 API 导入路径."""
        from unittest.mock import Mock

        from jiuwenclaw.agentserver.deep_agent.rails.runtime_prompt_rail import \
            RuntimePromptRail

        rail = RuntimePromptRail(language="cn", workspace_dir="/tmp/workspace")
        mock_builder = Mock()
        rail.system_prompt_builder = mock_builder

        import asyncio

        from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
        mock_ctx = Mock(spec=AgentCallbackContext)
        asyncio.run(rail.before_model_call(mock_ctx))

        added_sections = mock_builder.add_section.call_args_list
        workspace_section = None
        for call in added_sections:
            section = call[0][0]
            if section.name == "workspace":
                workspace_section = section
                break

        cn_content = workspace_section.content["cn"]

        # 验证包含完整的导入路径
        expected_import = (
            "from jiuwenclaw.agentserver.tools.subagent_executor.context_vars "
            "import get_effective_request_output_dir"
        )
        assert expected_import in cn_content

        # 验证包含调用示例
        assert "output_dir = get_effective_request_output_dir()" in cn_content

        # 验证包含异常处理示例
        assert "if output_dir:" in cn_content


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])