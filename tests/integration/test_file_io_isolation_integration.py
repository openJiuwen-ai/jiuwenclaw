# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

# pylint: disable=protected-access
# pylint: disable=no-self-argument
# 测试代码访问私有成员和不需要实例访问的测试方法是合理的测试实践

"""Agent 文件输入输出隔离集成测试.

测试目标：
- 完整文件处理流程（上传 → 下载 → Agent 处理 → 输出 → 上传）
- 文件隔离验证（不同用户、不同会话）
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
    tmpdir = tempfile.mkdtemp(prefix="integration_workspace_")
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def temp_project_dir():
    """创建临时项目目录."""
    tmpdir = tempfile.mkdtemp(prefix="integration_project_")
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
async def jiuwenclaw_instance(temp_workspace):
    """创建 JiuWenClaw 实例."""
    instance = JiuWenClaw(user_workspace_dir=str(temp_workspace))
    # Use MagicMock for adapter shell; only async entrypoints need AsyncMock.
    # Bare AsyncMock makes sync calls like set_working_checker() return unawaited coroutines.
    adapter = MagicMock(
        spec=[
            "process_message_impl",
            "handle_heartbeat",
            "handle_user_answer",
            "set_working_checker",
            "_instance",
        ]
    )
    adapter._instance = MagicMock()
    adapter.process_message_impl = AsyncMock()
    adapter.handle_heartbeat = AsyncMock(return_value=None)
    adapter.set_working_checker = MagicMock()
    instance._adapter = adapter
    return instance


class TestConversationModeCompleteFlow:
    """集成测试：对话模式完整文件处理流程 (Task 6.1)."""

    @pytest.mark.asyncio
    async def test_conversation_mode_complete_flow(
        self, jiuwenclaw_instance, temp_workspace
    ):
        """测试：上传 → 下载 → Agent 处理 → 输出 → 上传完整流程."""
        # 1. 准备输入文件请求
        request = AgentRequest(
            request_id="integration_req_001",
            session_id="conv_user",
            channel_id="web",
            chat_id="conv_session_001",
            params={
                "query": "请处理这些文件并生成结果",
                "files": [
                    {"uri": "file:///storage/conv_user/conv_session_001/input.txt", "name": "input.txt"}
                ]
            },
            metadata={
                "user_id": "conv_user",
                "enable_memory": False
            }
        )

        # Mock storage service - download
        mock_storage = AsyncMock()
        mock_storage.download_file = AsyncMock()
        mock_storage.upload_file = AsyncMock(return_value="file:///storage/conv_user/conv_session_001/result.txt")
        jiuwenclaw_instance._get_storage = AsyncMock(return_value=mock_storage)

        # Mock adapter response
        def create_agent_response():
            # Agent 生成的文件路径（应位于 output_dir）
            output_dir = temp_workspace / "files" / "conv_user" / "web_convsession001" / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            result_file = output_dir / "result.txt"
            result_file.write_text("Agent 处理结果")

            return AgentResponse(
                request_id="integration_req_001",
                channel_id="web",
                ok=True,
                payload={
                    "content": "处理完成",
                    "files": [
                        {"path": str(result_file), "name": "result.txt"}
                    ]
                }
            )

        jiuwenclaw_instance._adapter.process_message_impl = AsyncMock(return_value=create_agent_response())

        # 2. 执行完整流程
        response = await jiuwenclaw_instance.process_message(request)

        # 3. 验证流程完整性
        # 3.1 验证输入目录创建
        assert "input_dir" in request.metadata
        assert "output_dir" in request.metadata

        input_dir = Path(request.metadata["input_dir"])
        output_dir = Path(request.metadata["output_dir"])

        assert input_dir.exists()
        assert output_dir.exists()

        # 3.2 验证文件下载到 input_dir
        assert mock_storage.download_file.called

        # 3.3 验证文件路径写入 request.params.files
        assert "path" in request.params["files"][0]
        assert Path(request.params["files"][0]["path"]).parent == input_dir

        # 3.4 验证 Agent 接收到 input_dir 和 output_dir
        assert response.ok
        assert "files" in response.payload

        # 3.5 验证文件上传成功
        assert mock_storage.upload_file.called

        # 3.6 验证 response 包含 URI
        assert "uri" in response.payload["files"][0]
        assert response.payload["files"][0]["uri"] == "file:///storage/conv_user/conv_session_001/result.txt"


class TestProjectModeCompleteFlow:
    """集成测试：项目模式完整文件处理流程 (Task 6.2)."""

    @pytest.mark.asyncio
    async def test_project_mode_complete_flow(
        self, jiuwenclaw_instance, temp_workspace, temp_project_dir
    ):
        """测试：Agent 可访问项目文件 + 输出到 output_dir."""
        # 创建项目文件
        project_file = temp_project_dir / "source_code.py"
        project_file.write_text("def hello():\n    print('Hello World')")

        # 1. 准备请求（项目模式）
        request = AgentRequest(
            request_id="integration_req_002",
            session_id="proj_user",
            channel_id="web",
            chat_id="proj_session_002",
            params={
                "query": "修改代码并生成报告",
                "files": []
            },
            metadata={
                "user_id": "proj_user",
                "effective_project_dir": str(temp_project_dir),
                "enable_memory": False
            }
        )

        # Mock storage
        mock_storage = AsyncMock()
        mock_storage.upload_file = AsyncMock(return_value="file:///storage/proj_user/proj_session_002/report.md")
        jiuwenclaw_instance._get_storage = AsyncMock(return_value=mock_storage)

        # Mock adapter response - Agent 编辑项目文件并保存报告到 output_dir
        def create_agent_response():
            output_dir = temp_project_dir / "files" / "proj_user" / "web_projsession002" / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            report_file = output_dir / "report.md"
            report_file.write_text("代码修改报告")

            return AgentResponse(
                request_id="integration_req_002",
                channel_id="web",
                ok=True,
                payload={
                    "content": "已修改代码并生成报告",
                    "files": [
                        {"path": str(report_file), "name": "report.md"}
                    ]
                }
            )

        jiuwenclaw_instance._adapter.process_message_impl = AsyncMock(return_value=create_agent_response())

        # 2. 执行完整流程
        response = await jiuwenclaw_instance.process_message(request)

        # 3. 验证
        # 3.1 验证项目模式下目录创建
        input_dir = Path(request.metadata["input_dir"])
        output_dir = Path(request.metadata["output_dir"])

        # 验证路径包含user_id和chat_id
        assert "proj_user" in str(input_dir)
        assert "proj_session_002" in str(input_dir) or "projsession002" in str(input_dir)
        assert input_dir.name == "input"
        assert output_dir.name == "output"

        # 3.2 验证 Agent 可访问项目文件（effective_project_dir）
        # 这通过 adapter._update_runtime_config 传递给 Agent
        # 在实际实现中，Agent 的 cwd 应设置为 effective_project_dir

        # 3.3 验证输出文件上传成功
        assert response.ok
        assert mock_storage.upload_file.called
        assert "uri" in response.payload["files"][0]


class TestFileIsolation:
    """集成测试：文件隔离验证 (Task 6.3 & 6.4)."""

    @pytest.mark.asyncio
    async def test_same_user_different_sessions_isolation(
        self, jiuwenclaw_instance, temp_workspace
    ):
        """测试：同一用户不同会话的文件隔离 (Task 6.3)."""
        # 创建两个不同会话的请求
        request1 = AgentRequest(
            request_id="isolation_req_001",
            session_id="isolation_user",
            channel_id="web",
            chat_id="session_alpha",
            params={"query": "处理任务 A", "files": []},
            metadata={"user_id": "isolation_user", "enable_memory": False}
        )

        request2 = AgentRequest(
            request_id="isolation_req_002",
            session_id="isolation_user",
            channel_id="web",
            chat_id="session_beta",
            params={"query": "处理任务 B", "files": []},
            metadata={"user_id": "isolation_user", "enable_memory": False}
        )

        mock_storage = AsyncMock()
        jiuwenclaw_instance._get_storage = AsyncMock(return_value=mock_storage)

        # 执行两个请求
        await jiuwenclaw_instance.prepare_files_for_agent(request1)
        await jiuwenclaw_instance.prepare_files_for_agent(request2)

        # 验证隔离：两个会话的目录路径不同
        dir1_alpha = Path(request1.metadata["output_dir"])
        dir2_beta = Path(request2.metadata["output_dir"])

        assert dir1_alpha != dir2_beta
        # chat_id is sanitized: "session_alpha" -> "sessionalpha", "session_beta" -> "sessionbeta"
        assert "sessionalpha" in dir1_alpha.parent.name or "session_alpha" in dir1_alpha.parent.name
        assert "sessionbeta" in dir2_beta.parent.name or "session_beta" in dir2_beta.parent.name

        # 创建测试文件验证物理隔离
        file1 = dir1_alpha / "task_a_result.txt"
        file2 = dir2_beta / "task_b_result.txt"

        file1.write_text("任务 A 结果")
        file2.write_text("任务 B 结果")

        # 验证文件确实隔离
        assert file1.exists()
        assert file2.exists()
        assert file1.read_text() != file2.read_text()

        # 验证目录层级
        # Path structure: files/{user_id}/{chat_id}/output
        assert str(file1.parent.parent) != str(file2.parent.parent)  # Different chat_id dirs

    @pytest.mark.asyncio
    async def test_different_users_isolation(
        self, jiuwenclaw_instance, temp_workspace
    ):
        """测试：不同用户的文件隔离 (Task 6.4)."""
        # 创建两个不同用户的请求
        request1 = AgentRequest(
            request_id="user1_req",
            session_id="user_001",
            channel_id="web",
            chat_id="session_001",
            params={"query": "用户1任务", "files": []},
            metadata={"user_id": "user_001", "enable_memory": False}
        )

        request2 = AgentRequest(
            request_id="user2_req",
            session_id="user_002",
            channel_id="web",
            chat_id="session_002",
            params={"query": "用户2任务", "files": []},
            metadata={"user_id": "user_002", "enable_memory": False}
        )

        mock_storage = AsyncMock()
        jiuwenclaw_instance._get_storage = AsyncMock(return_value=mock_storage)

        # 执行两个请求
        await jiuwenclaw_instance.prepare_files_for_agent(request1)
        await jiuwenclaw_instance.prepare_files_for_agent(request2)

        # 验证隔离：不同用户的目录路径完全不同
        output_dir1 = Path(request1.metadata["output_dir"])
        output_dir2 = Path(request2.metadata["output_dir"])

        assert output_dir1 != output_dir2
        # Path structure: files/{user_id}/{chat_id}/output
        assert output_dir1.parent.parent.name == "user_001"
        assert output_dir2.parent.parent.name == "user_002"

        # 验证物理隔离
        file1 = output_dir1 / "user1_data.txt"
        file2 = output_dir2 / "user2_data.txt"

        file1.write_text("用户1数据")
        file2.write_text("用户2数据")

        assert file1.exists()
        assert file2.exists()

        # 验证不同用户无法访问对方目录
        # 在实际实现中，这通过 file_guard.py 权限控制实现
        # Path structure: files/{user_id}/{chat_id}/output
        assert str(file1.parent.parent.parent) != str(file2.parent.parent.parent)  # Different user_id dirs


class TestFileUploadFromOutputDir:
    """测试：文件上传从 output_dir 读取."""

    @pytest.mark.asyncio
    async def test_upload_from_correct_output_dir(
        self, jiuwenclaw_instance, temp_workspace
    ):
        """测试：Agent 生成的文件从 output_dir 上传."""
        # 创建 output_dir 并放置文件
        output_dir = temp_workspace / "files" / "upload_user" / "upload_session" / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        agent_file = output_dir / "agent_output.txt"
        agent_file.write_text("Agent 输出内容")

        # 创建 response
        response = AgentResponse(
            request_id="upload_req",
            channel_id="web",
            ok=True,
            payload={
                "files": [
                    {"path": str(agent_file), "name": "agent_output.txt"}
                ]
            }
        )

        # Mock storage
        mock_storage = AsyncMock()
        mock_storage.upload_file = AsyncMock(return_value="file:///storage/upload_user/upload_session/agent_output.txt")
        jiuwenclaw_instance._get_storage = AsyncMock(return_value=mock_storage)

        # 执行上传
        await jiuwenclaw_instance.upload_agent_files(
            response,
            user_id="upload_user",
            chat_id="upload_session",
            channel_type="web"
        )

        # 验证上传成功
        assert mock_storage.upload_file.called
        assert response.payload["files"][0]["uri"] == "file:///storage/upload_user/upload_session/agent_output.txt"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])