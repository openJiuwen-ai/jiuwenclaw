"""文本Formatter格式单元测试"""

import logging
import pytest
from jiuwenclaw.utils import UserVisibleTagFilter, LoggingTagConfig


class TestTextFormatterFormat:
    """测试新格式字符串的输出位置和空格处理"""

    def test_formatter_with_user_tag(self):
        """测试带[USER]标签的formatter输出格式"""
        # 创建formatter（使用新格式）
        formatter = logging.Formatter(
            fmt="%(levelname)s %(user_tag)s%(name)s: %(message)s"
        )

        # 创建record并设置user_tag
        record = logging.LogRecord(
            "test.logger", logging.INFO, "", 0, "测试消息", (), None
        )
        record.user_tag = "[USER] "

        # 格式化输出
        output = formatter.format(record)

        # 验证格式正确
        assert output == "INFO [USER] test.logger: 测试消息"

    def test_formatter_without_user_tag(self):
        """测试不带标签的formatter输出格式"""
        formatter = logging.Formatter(
            fmt="%(levelname)s %(user_tag)s%(name)s: %(message)s"
        )

        record = logging.LogRecord(
            "test.logger", logging.INFO, "", 0, "测试消息", (), None
        )
        record.user_tag = ""  # 空标签

        output = formatter.format(record)

        # 验证格式正确（无多余空格）
        assert output == "INFO test.logger: 测试消息"

    def test_formatter_with_user_progress_tag(self):
        """测试带[USER_PROGRESS]标签的formatter输出格式"""
        formatter = logging.Formatter(
            fmt="%(levelname)s %(user_tag)s%(name)s: %(message)s"
        )

        record = logging.LogRecord(
            "test.logger", logging.INFO, "", 0, "测试消息", (), None
        )
        record.user_tag = "[USER_PROGRESS] "

        output = formatter.format(record)

        # 验证格式正确
        assert output == "INFO [USER_PROGRESS] test.logger: 测试消息"

    def test_formatter_tag_position(self):
        """测试tag位置在logger名称前面"""
        formatter = logging.Formatter(
            fmt="%(levelname)s %(user_tag)s%(name)s: %(message)s"
        )

        record = logging.LogRecord(
            "jiuwenclaw.gateway.channel_manager", logging.INFO, "", 0,
            "已派发到 Channel", (), None
        )
        record.user_tag = "[USER] "

        output = formatter.format(record)

        # 验证tag在logger名称前
        assert output == "INFO [USER] jiuwenclaw.gateway.channel_manager: 已派发到 Channel"

    def test_formatter_no_extra_spaces(self):
        """测试空标签时无多余空格"""
        formatter = logging.Formatter(
            fmt="%(levelname)s %(user_tag)s%(name)s: %(message)s"
        )

        record = logging.LogRecord(
            "test.logger", logging.WARNING, "", 0, "警告消息", (), None
        )
        record.user_tag = ""  # 空标签

        output = formatter.format(record)

        # 验证无多余空格（不会出现"WARNING  test.logger"的情况）
        assert output == "WARNING test.logger: 警告消息"
        assert not output.startswith("WARNING  ")  # 确保没有双空格

    def test_formatter_timestamp_format(self):
        """测试完整的时间戳格式包含user_tag"""
        formatter = logging.Formatter(
            fmt="%(asctime)s.%(msecs)03d %(levelname)s %(user_tag)s%(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        record = logging.LogRecord(
            "test.logger", logging.INFO, "", 0, "消息", (), None
        )
        record.user_tag = "[USER] "

        output = formatter.format(record)

        # 验证完整格式
        assert "[USER] test.logger: 消息" in output
        assert "INFO" in output
        # 验证时间戳格式正确（YYYY-MM-DD HH:MM:SS.mmm格式）
        assert "2026-" in output or "2025-" in output  # 年份部分
        assert "." in output  # 毫秒分隔符存在

    def test_formatter_handles_different_log_levels(self):
        """测试formatter处理不同日志级别"""
        formatter = logging.Formatter(
            fmt="%(levelname)s %(user_tag)s%(name)s: %(message)s"
        )

        levels = [
            (logging.DEBUG, "DEBUG"),
            (logging.INFO, "INFO"),
            (logging.WARNING, "WARNING"),
            (logging.ERROR, "ERROR"),
            (logging.CRITICAL, "CRITICAL"),
        ]

        for level, level_name in levels:
            record = logging.LogRecord(
                "test.logger", level, "", 0, "消息", (), None
            )
            record.user_tag = "[USER] "

            output = formatter.format(record)

            # 验证级别名称正确
            assert output.startswith(level_name)
            # 验证tag存在
            assert "[USER] test.logger: 消息" in output