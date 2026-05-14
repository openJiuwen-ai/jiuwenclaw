# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Storage 工具函数单元测试."""

import pytest

from jiuwenclaw.storage.utils import (
    sanitize_chat_id,
    build_chat_prefix,
    build_object_key,
)
from jiuwenclaw.storage.exceptions import StorageError


class TestSanitizeChatId:
    """测试 sanitize_chat_id() 函数."""

    @staticmethod
    def test_web_channel_valid_chat_id():
        """测试 WebChannel 有效的 chat_id."""
        result = sanitize_chat_id("sess_abc123", "web")
        assert result == "sess_abc123"

    @staticmethod
    def test_web_channel_chat_id_with_dots():
        """测试 WebChannel 包含点的 chat_id."""
        result = sanitize_chat_id("sess_abc.123", "web")
        assert result == "sess_abc123"

    @staticmethod
    def test_dingtalk_channel_valid_chat_id():
        """测试 DingTalk 有效的 chat_id."""
        result = sanitize_chat_id("cid12h3g5j78k9", "dingtalk")
        assert result == "cid12h3g5j78k9"

    @staticmethod
    def test_dingtalk_channel_chat_id_with_special_chars():
        """测试 DingTalk 包含特殊字符的 chat_id."""
        result = sanitize_chat_id("cid12h3/g5j78k9", "dingtalk")
        assert result == "cid12h3g5j78k9"

    @staticmethod
    def test_wecom_channel_valid_chat_id():
        """测试 Wecom 有效的 chat_id."""
        result = sanitize_chat_id("wrwBGK8VwAA2LxXaGJ8H4", "wecom")
        assert result == "wrwBGK8VwAA2LxXaGJ8H4"

    @staticmethod
    def test_wecom_channel_chat_id_with_slash():
        """测试 Wecom 包含斜杠的 chat_id."""
        result = sanitize_chat_id("wrwBGK8VwAA/2LxXaGJ8H4", "wecom")
        assert result == "wrwBGK8VwAA2LxXaGJ8H4"

    @staticmethod
    def test_xiaoyi_channel_valid_chat_id():
        """测试 XiaoYi 有效的 chat_id."""
        result = sanitize_chat_id("xy_session_1715552000", "xiaoyi")
        assert result == "xy_session_1715552000"

    @staticmethod
    def test_empty_chat_id_raises_error():
        """测试空 chat_id 抛出异常."""
        with pytest.raises(ValueError, match="chat_id 不能为空"):
            sanitize_chat_id("", "web")

    @staticmethod
    def test_none_chat_id_raises_error():
        """测试 None chat_id 抛出异常."""
        with pytest.raises(ValueError, match="chat_id 不能为空"):
            sanitize_chat_id(None, "web")

    @staticmethod
    def test_empty_channel_type_raises_error():
        """测试空 channel_type 抛出异常."""
        with pytest.raises(ValueError, match="channel_type 不能为空"):
            sanitize_chat_id("sess_abc", "")

    @staticmethod
    def test_special_chars_only_results_in_error():
        """测试仅包含特殊字符的 chat_id 清理后抛出异常."""
        with pytest.raises(ValueError, match="清理后为空字符串"):
            sanitize_chat_id("@#$%", "web")

    @staticmethod
    def test_unknown_channel_uses_generic_cleanup():
        """测试未知 channel 使用通用清理策略."""
        result = sanitize_chat_id("unknown-ch.at_id", "unknown")
        assert result == "unknown-chat_id"


class TestBuildChatPrefix:
    """测试 build_chat_prefix() 函数."""

    @staticmethod
    def test_build_web_chat_prefix():
        """测试构建 WebChannel chat 前缀."""
        result = build_chat_prefix("web", "sess_abc123")
        assert result == "web_sess_abc123"

    @staticmethod
    def test_build_dingtalk_chat_prefix():
        """测试构建 DingTalk chat 前缀."""
        result = build_chat_prefix("dingtalk", "cid123456")
        assert result == "dingtalk_cid123456"

    @staticmethod
    def test_build_xiaoyi_chat_prefix():
        """测试构建 XiaoYi chat 前缀."""
        result = build_chat_prefix("xiaoyi", "xy_session_001")
        assert result == "xiaoyi_xy_session_001"

    @staticmethod
    def test_build_wecom_chat_prefix():
        """测试构建 Wecom chat 前缀."""
        result = build_chat_prefix("wecom", "wr987654321")
        assert result == "wecom_wr987654321"

    @staticmethod
    def test_empty_channel_type_raises_error():
        """测试空 channel_type 抛出异常."""
        with pytest.raises(ValueError, match="channel_type 不能为空"):
            build_chat_prefix("", "sess_abc")

    @staticmethod
    def test_empty_chat_id_raises_error():
        """测试空 chat_id 抛出异常."""
        with pytest.raises(ValueError, match="chat_id 不能为空"):
            build_chat_prefix("web", "")


class TestBuildObjectKey:
    """测试 build_object_key() 函数."""

    @staticmethod
    def test_build_web_object_key():
        """测试构建 WebChannel 对象 Key."""
        result = build_object_key(
            user_id="user123",
            channel_type="web",
            chat_id="sess_abc123",
            timestamp="20250513_120000",
            filename="document.pdf"
        )
        assert result == "files/user123/web_sess_abc123/20250513_120000/document.pdf"

    @staticmethod
    def test_build_dingtalk_object_key():
        """测试构建 DingTalk 对象 Key."""
        result = build_object_key(
            user_id="user456",
            channel_type="dingtalk",
            chat_id="cid123456",
            timestamp="20250513_130000",
            filename="file.docx"
        )
        assert result == "files/user456/dingtalk_cid123456/20250513_130000/file.docx"

    @staticmethod
    def test_build_xiaoyi_object_key():
        """测试构建 XiaoYi 对象 Key."""
        result = build_object_key(
            user_id="user789",
            channel_type="xiaoyi",
            chat_id="xy_session_001",
            timestamp="20250513_140000",
            filename="data.json"
        )
        assert result == "files/user789/xiaoyi_xy_session_001/20250513_140000/data.json"

    @staticmethod
    def test_build_wecom_object_key():
        """测试构建 Wecom 对象 Key."""
        result = build_object_key(
            user_id="user101",
            channel_type="wecom",
            chat_id="wr987654321",
            timestamp="20250513_150000",
            filename="report.xlsx"
        )
        assert result == "files/user101/wecom_wr987654321/20250513_150000/report.xlsx"

    @staticmethod
    def test_empty_user_id_raises_error():
        """测试空 user_id 抛出异常."""
        with pytest.raises(ValueError, match="user_id 不能为空"):
            build_object_key("", "web", "sess", "20250513_120000", "file.pdf")

    @staticmethod
    def test_empty_channel_type_raises_error():
        """测试空 channel_type 抛出异常."""
        with pytest.raises(ValueError, match="channel_type 不能为空"):
            build_object_key("user123", "", "sess", "20250513_120000", "file.pdf")

    @staticmethod
    def test_empty_chat_id_raises_error():
        """测试空 chat_id 抛出异常."""
        with pytest.raises(ValueError, match="chat_id 不能为空"):
            build_object_key("user123", "web", "", "20250513_120000", "file.pdf")

    @staticmethod
    def test_empty_timestamp_raises_error():
        """测试空 timestamp 抛出异常."""
        with pytest.raises(ValueError, match="timestamp 不能为空"):
            build_object_key("user123", "web", "sess", "", "file.pdf")

    @staticmethod
    def test_empty_filename_raises_error():
        """测试空 filename 抛出异常."""
        with pytest.raises(ValueError, match="filename 不能为空"):
            build_object_key("user123", "web", "sess", "20250513_120000", "")