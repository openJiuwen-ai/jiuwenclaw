# Copyright (c) Huawei Technologies, Co., Ltd. 2025. All rights reserved.
"""Tests for open_ability_utils module."""
import json
import builtins
import os
from unittest.mock import mock_open, MagicMock, patch

import pytest
import jiuwenclaw.agentserver.open_ability_utils as oa_utils


@pytest.fixture(autouse=True)
def reset_globals():
    oa_utils._SANDBOX_ID = None
    oa_utils._API_KEY = None
    original_debug = oa_utils._SANDBOX_INIT_DATA_DEBUG
    yield
    oa_utils._SANDBOX_ID = None
    oa_utils._API_KEY = None
    oa_utils._SANDBOX_INIT_DATA_DEBUG = original_debug


class TestGetSandboxInitDataSecurity:
    """测试初始化数据的安全读取和删除功能"""

    def test_file_deleted_after_read_by_default(self, monkeypatch):
        """默认情况下（非Debug模式），读取后应自动删除文件"""
        fake_path = "/secure/config.json"
        fake_data = {"apiKey": "secret_key", "sandboxId": "sandbox_123"}

        monkeypatch.setenv("SANDBOX_INIT_DATA_PATH", fake_path)
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        # 确保 _SANDBOX_INIT_DATA_DEBUG 为 False
        monkeypatch.setattr(oa_utils, "_SANDBOX_INIT_DATA_DEBUG", False)

        m_open = mock_open()
        monkeypatch.setattr(builtins, "open", m_open)
        monkeypatch.setattr(json, "load", lambda f: fake_data)

        mock_remove = MagicMock()
        monkeypatch.setattr(os, "remove", mock_remove)

        mock_logger = MagicMock()
        monkeypatch.setattr(oa_utils, "logger", mock_logger)

        oa_utils.get_sandbox_init_data()

        # 验证文件被正确读取
        m_open.assert_called_once_with(fake_path, "r", encoding="utf-8")
        # 验证文件被删除
        mock_remove.assert_called_once_with(fake_path)
        # 验证日志记录
        mock_logger.info.assert_any_call(
            "[SandboxInitData] 成功读取初始化数据: sandboxId=%s", "sandbox_123"
        )
        mock_logger.info.assert_any_call(
            "[SandboxInitData] 已安全删除初始化文件: %s", fake_path
        )

    def test_file_preserved_in_debug_mode(self, monkeypatch):
        """Debug模式下，读取后应保留文件"""
        fake_path = "/debug/config.json"
        fake_data = {"apiKey": "secret_key", "sandboxId": "sandbox_456"}

        monkeypatch.setenv("SANDBOX_INIT_DATA_PATH", fake_path)
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        # 启用 Debug 模式
        monkeypatch.setattr(oa_utils, "_SANDBOX_INIT_DATA_DEBUG", True)

        m_open = mock_open()
        monkeypatch.setattr(builtins, "open", m_open)
        monkeypatch.setattr(json, "load", lambda f: fake_data)

        mock_remove = MagicMock()
        monkeypatch.setattr(os, "remove", mock_remove)

        mock_logger = MagicMock()
        monkeypatch.setattr(oa_utils, "logger", mock_logger)

        oa_utils.get_sandbox_init_data()

        # 验证文件被正确读取
        m_open.assert_called_once_with(fake_path, "r", encoding="utf-8")
        # 验证文件**未被**删除
        mock_remove.assert_not_called()
        # 验证日志记录 Debug 模式警告
        mock_logger.warning.assert_called_once_with(
            "[SandboxInitData] Debug模式已开启，保留初始化文件: %s", fake_path
        )

    def test_delete_failure_logged_as_warning(self, monkeypatch):
        """文件删除失败时应记录警告日志"""
        fake_path = "/secure/config.json"
        fake_data = {"apiKey": "secret_key", "sandboxId": "sandbox_789"}

        monkeypatch.setenv("SANDBOX_INIT_DATA_PATH", fake_path)
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        monkeypatch.setattr(oa_utils, "_SANDBOX_INIT_DATA_DEBUG", False)

        m_open = mock_open()
        monkeypatch.setattr(builtins, "open", m_open)
        monkeypatch.setattr(json, "load", lambda f: fake_data)

        # 模拟删除失败
        mock_remove = MagicMock(side_effect=PermissionError("Permission denied"))
        monkeypatch.setattr(os, "remove", mock_remove)

        mock_logger = MagicMock()
        monkeypatch.setattr(oa_utils, "logger", mock_logger)

        oa_utils.get_sandbox_init_data()

        # 验证尝试删除文件
        mock_remove.assert_called_once_with(fake_path)
        # 验证警告日志
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args[0]
        assert "删除初始化文件失败" in call_args[0]
        assert "Permission denied" in str(call_args[1])

    def test_no_delete_if_read_fails(self, monkeypatch):
        """读取失败时不应尝试删除文件"""
        fake_path = "/bad/config.json"

        monkeypatch.setenv("SANDBOX_INIT_DATA_PATH", fake_path)
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        monkeypatch.setattr(oa_utils, "_SANDBOX_INIT_DATA_DEBUG", False)

        m_open = mock_open()
        monkeypatch.setattr(builtins, "open", m_open)
        # 模拟 JSON 解析失败
        monkeypatch.setattr(json, "load", MagicMock(side_effect=json.JSONDecodeError("bad json", "", 0)))

        mock_remove = MagicMock()
        monkeypatch.setattr(os, "remove", mock_remove)

        mock_logger = MagicMock()
        monkeypatch.setattr(oa_utils, "logger", mock_logger)

        oa_utils.get_sandbox_init_data()

        # 验证文件从未被删除
        mock_remove.assert_not_called()
        # 验证错误日志
        mock_logger.error.assert_called_once()
        assert "初始化数据获取失败" in mock_logger.error.call_args[0][0]


class TestGettersWithSecurity:
    """测试 getter 函数在安全模式下的行为"""

    def test_get_api_key_triggers_delete(self, monkeypatch):
        """get_api_key 调用应触发文件删除"""
        fake_path = "/secure/api_key.json"
        fake_data = {"apiKey": "my_secret_key", "sandboxId": "my_sandbox"}

        monkeypatch.setenv("SANDBOX_INIT_DATA_PATH", fake_path)
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        monkeypatch.setattr(oa_utils, "_SANDBOX_INIT_DATA_DEBUG", False)

        m_open = mock_open()
        monkeypatch.setattr(builtins, "open", m_open)
        monkeypatch.setattr(json, "load", lambda f: fake_data)

        mock_remove = MagicMock()
        monkeypatch.setattr(os, "remove", mock_remove)

        result = oa_utils.get_api_key()

        assert result == "my_secret_key"
        mock_remove.assert_called_once_with(fake_path)

    def test_get_sandbox_id_triggers_delete(self, monkeypatch):
        """get_sandbox_id 调用应触发文件删除"""
        fake_path = "/secure/sandbox.json"
        fake_data = {"apiKey": "my_key", "sandboxId": "my_sandbox_id"}

        monkeypatch.setenv("SANDBOX_INIT_DATA_PATH", fake_path)
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        monkeypatch.setattr(oa_utils, "_SANDBOX_INIT_DATA_DEBUG", False)

        m_open = mock_open()
        monkeypatch.setattr(builtins, "open", m_open)
        monkeypatch.setattr(json, "load", lambda f: fake_data)

        mock_remove = MagicMock()
        monkeypatch.setattr(os, "remove", mock_remove)

        result = oa_utils.get_sandbox_id()

        assert result == "my_sandbox_id"
        mock_remove.assert_called_once_with(fake_path)

    def test_getters_only_delete_once(self, monkeypatch):
        """多次调用 getter 应只删除一次文件"""
        fake_path = "/secure/data.json"
        fake_data = {"apiKey": "key", "sandboxId": "id"}

        monkeypatch.setenv("SANDBOX_INIT_DATA_PATH", fake_path)
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        monkeypatch.setattr(oa_utils, "_SANDBOX_INIT_DATA_DEBUG", False)

        m_open = mock_open()
        monkeypatch.setattr(builtins, "open", m_open)
        monkeypatch.setattr(json, "load", lambda f: fake_data)

        mock_remove = MagicMock()
        monkeypatch.setattr(os, "remove", mock_remove)

        # 第一次调用
        oa_utils.get_api_key()
        assert mock_remove.call_count == 1

        # 第二次调用（使用缓存值）
        oa_utils.get_sandbox_id()
        # 删除不应再次被调用
        assert mock_remove.call_count == 1


class TestInitOaMessage:
    """测试 init_oa_message 函数"""

    def test_init_message_type(self):
        """INIT 消息类型"""
        result = oa_utils.init_oa_message("INIT")
        assert result["msgType"] == "INIT"
        assert result["msgDetail"] == "{}"

    def test_heartbeat_message_type(self):
        """HEARTBEAT 消息类型"""
        result = oa_utils.init_oa_message("HEARTBEAT")
        assert result["msgType"] == "HEARTBEAT"
        assert result["msgDetail"] == "{}"

    def test_message_with_data(self):
        """MESSAGE 类型携带数据"""
        data = {"service_id": "svc_123", "request_id": "req_456", "payload": "test"}
        result = oa_utils.init_oa_message("MESSAGE", data)
        assert result["msgType"] == "MESSAGE"
        assert result["sessionId"] == "svc_123"
        assert result["taskId"] == "req_456"
        assert "test" in result["msgDetail"]

    def test_invalid_message_type(self):
        """无效消息类型应抛出异常"""
        with pytest.raises(Exception) as exc_info:
            oa_utils.init_oa_message("INVALID")
        assert "Invalid msg_type" in str(exc_info.value)


class TestGetOaAuthHeaders:
    """测试 get_oa_auth_headers 函数"""

    def test_headers_returned_when_data_available(self, monkeypatch):
        """当 api_key 和 sandbox_id 可用时，返回正确的 headers"""
        fake_path = "/secure/auth.json"
        fake_data = {"apiKey": "secret_123", "sandboxId": "box_456"}

        monkeypatch.setenv("SANDBOX_INIT_DATA_PATH", fake_path)
        monkeypatch.setattr(os.path, "exists", lambda p: True)
        monkeypatch.setattr(oa_utils, "_SANDBOX_INIT_DATA_DEBUG", False)

        m_open = mock_open()
        monkeypatch.setattr(builtins, "open", m_open)
        monkeypatch.setattr(json, "load", lambda f: fake_data)
        monkeypatch.setattr(os, "remove", MagicMock())

        # 由于函数会无限轮询，我们需要预先设置好值
        oa_utils._API_KEY = "secret_123"
        oa_utils._SANDBOX_ID = "box_456"

        headers = oa_utils.get_oa_auth_headers(retry_interval=0.01)

        assert headers["x-api-key"] == "secret_123"
        assert headers["x-sandbox-id"] == "box_456"
        assert headers["x-request-from"] == "jiuwenclaw"
        assert headers["x-hag-trace-id"] == "box_456"  # 默认使用 sandbox_id

    def test_headers_with_custom_session_id(self, monkeypatch):
        """使用自定义 session_id 作为 trace-id"""
        oa_utils._API_KEY = "key"
        oa_utils._SANDBOX_ID = "box"

        headers = oa_utils.get_oa_auth_headers(retry_interval=0.01, session_id="custom_trace")

        assert headers["x-hag-trace-id"] == "custom_trace"

    def test_polling_logs_warning_first_time(self, monkeypatch):
        """首次轮询时记录警告日志"""
        oa_utils._API_KEY = None
        oa_utils._SANDBOX_ID = None

        mock_logger = MagicMock()
        monkeypatch.setattr(oa_utils, "logger", mock_logger)

        # 模拟在第一次检查后设置值
        call_count = 0

        def mock_get_api_key():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                return "key"
            return None

        def mock_get_sandbox_id():
            nonlocal call_count
            if call_count >= 2:
                return "box"
            return None

        monkeypatch.setattr(oa_utils, "get_api_key", mock_get_api_key)
        monkeypatch.setattr(oa_utils, "get_sandbox_id", mock_get_sandbox_id)

        # 使用很小的间隔避免测试太慢
        import time
        start = time.time()
        headers = oa_utils.get_oa_auth_headers(retry_interval=0.001)
        elapsed = time.time() - start

        # 验证确实进行了轮询
        assert call_count >= 2
        # 验证记录了警告日志
        mock_logger.warning.assert_called_once()
        assert "鉴权信息不完整" in mock_logger.warning.call_args[0][0]
