"""UserVisibleTagFilter单元测试"""

import logging
import pytest
from jiuwenclaw.utils import UserVisibleTagFilter, LoggingTagConfig


class TestUserVisibleTagFilter:
    """测试UserVisibleTagFilter的record.user_tag字段设置"""

    def test_filter_sets_user_tag_field_for_critical(self):
        """测试user_visible='critical'时设置[USER]标签"""
        # 创建启用配置
        config = LoggingTagConfig()
        config.user_visible = True
        config.user_progress_visible = True

        filter_obj = UserVisibleTagFilter(config)
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "测试消息", (), None
        )
        record.user_visible = "critical"

        result = filter_obj.filter(record)

        assert result is True
        assert hasattr(record, 'user_tag')
        assert record.user_tag == "[USER] "

    def test_filter_sets_user_tag_field_for_progress(self):
        """测试user_visible='progress'时设置[USER_PROGRESS]标签"""
        config = LoggingTagConfig()
        config.user_visible = True
        config.user_progress_visible = True

        filter_obj = UserVisibleTagFilter(config)
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "测试消息", (), None
        )
        record.user_visible = "progress"

        result = filter_obj.filter(record)

        assert result is True
        assert hasattr(record, 'user_tag')
        assert record.user_tag == "[USER_PROGRESS] "

    def test_filter_sets_empty_tag_for_no_user_visible(self):
        """测试无user_visible属性时设置空标签"""
        config = LoggingTagConfig()
        config.user_visible = True
        config.user_progress_visible = True

        filter_obj = UserVisibleTagFilter(config)
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "测试消息", (), None
        )
        # 不设置user_visible属性

        result = filter_obj.filter(record)

        assert result is True
        assert hasattr(record, 'user_tag')
        assert record.user_tag == ""

    def test_filter_sets_empty_tag_for_unknown_value(self):
        """测试未知user_visible值时设置空标签"""
        config = LoggingTagConfig()
        config.user_visible = True
        config.user_progress_visible = True

        filter_obj = UserVisibleTagFilter(config)
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "测试消息", (), None
        )
        record.user_visible = "unknown_value"

        result = filter_obj.filter(record)

        assert result is True
        assert hasattr(record, 'user_tag')
        assert record.user_tag == ""

    def test_filter_respects_config_disabled_user_visible(self):
        """测试配置禁用user_visible时不设置[USER]标签"""
        config = LoggingTagConfig()
        config.user_visible = False  # 禁用
        config.user_progress_visible = True

        filter_obj = UserVisibleTagFilter(config)
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "测试消息", (), None
        )
        record.user_visible = "critical"

        result = filter_obj.filter(record)

        assert result is True
        assert hasattr(record, 'user_tag')
        assert record.user_tag == ""  # 配置禁用，应该是空字符串

    def test_filter_respects_config_disabled_user_progress_visible(self):
        """测试配置禁用user_progress_visible时不设置[USER_PROGRESS]标签"""
        config = LoggingTagConfig()
        config.user_visible = True
        config.user_progress_visible = False  # 禁用

        filter_obj = UserVisibleTagFilter(config)
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "测试消息", (), None
        )
        record.user_visible = "progress"

        result = filter_obj.filter(record)

        assert result is True
        assert hasattr(record, 'user_tag')
        assert record.user_tag == ""  # 配置禁用，应该是空字符串

    def test_filter_does_not_modify_record_msg(self):
        """测试filter不修改record.msg字段"""
        config = LoggingTagConfig()
        config.user_visible = True

        filter_obj = UserVisibleTagFilter(config)
        original_message = "原始消息内容"
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, original_message, (), None
        )
        record.user_visible = "critical"

        filter_obj.filter(record)

        # 验证msg字段未被修改
        assert record.msg == original_message
        # 验证user_tag字段被设置
        assert record.user_tag == "[USER] "