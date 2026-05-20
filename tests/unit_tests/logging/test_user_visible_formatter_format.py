"""文本Formatter格式单元测试"""

import logging
from unittest.mock import patch

import pytest

from jiuwenclaw.utils import UserVisibleTagFilter, LoggingTagConfig


class TestTextFormatterFormat:
    """测试新格式字符串的输出位置和空格处理"""

    @staticmethod
    def test_formatter_with_user_tag():
        """测试带[USER]标签的formatter输出格式（包含进程ID和行号）"""
        # Mock固定进程ID为12345
        with patch('os.getpid', return_value=12345):
            # 创建formatter（使用新格式）
            formatter = logging.Formatter(
                fmt="[%(process)d] %(levelname)s %(user_tag)s%(name)s:%(lineno)d: %(message)s"
            )

            # 创建record并设置user_tag（lineno=88）
            record = logging.LogRecord(
                "test.logger", logging.INFO, "", 88, "测试消息", (), None
            )
            record.user_tag = "[USER] "

            # 格式化输出
            output = formatter.format(record)

            # 验证格式正确（包含进程ID和行号）
            assert output == "[12345] INFO [USER] test.logger:88: 测试消息"

    @staticmethod
    def test_formatter_without_user_tag():
        """测试不带标签的formatter输出格式（包含进程ID和行号）"""
        with patch('os.getpid', return_value=12345):
            formatter = logging.Formatter(
                fmt="[%(process)d] %(levelname)s %(user_tag)s%(name)s:%(lineno)d: %(message)s"
            )

            record = logging.LogRecord(
                "test.logger", logging.INFO, "", 125, "测试消息", (), None
            )
            record.user_tag = ""  # 空标签

            output = formatter.format(record)

            # 验证格式正确（无多余空格，包含进程ID和行号）
            assert output == "[12345] INFO test.logger:125: 测试消息"

    @staticmethod
    def test_formatter_with_user_progress_tag():
        """测试带[USER_PROGRESS]标签的formatter输出格式（包含进程ID和行号）"""
        with patch('os.getpid', return_value=12345):
            formatter = logging.Formatter(
                fmt="[%(process)d] %(levelname)s %(user_tag)s%(name)s:%(lineno)d: %(message)s"
            )

            record = logging.LogRecord(
                "test.logger", logging.INFO, "", 1024, "测试消息", (), None
            )
            record.user_tag = "[USER_PROGRESS] "

            output = formatter.format(record)

            # 验证格式正确（包含进程ID和行号）
            assert output == "[12345] INFO [USER_PROGRESS] test.logger:1024: 测试消息"

    @staticmethod
    def test_formatter_tag_position():
        """测试tag位置在logger名称前面（包含进程ID和行号）"""
        with patch('os.getpid', return_value=12345):
            formatter = logging.Formatter(
                fmt="[%(process)d] %(levelname)s %(user_tag)s%(name)s:%(lineno)d: %(message)s"
            )

            record = logging.LogRecord(
                "jiuwenclaw.gateway.channel_manager", logging.INFO, "", 88,
                "已派发到 Channel", (), None
            )
            record.user_tag = "[USER] "

            output = formatter.format(record)

            # 验证tag在logger名称前，进程ID在levelname前，lineno在logger后
            assert output == "[12345] INFO [USER] jiuwenclaw.gateway.channel_manager:88: 已派发到 Channel"

    @staticmethod
    def test_formatter_no_extra_spaces():
        """测试空标签时无多余空格（包含进程ID和行号）"""
        with patch('os.getpid', return_value=12345):
            formatter = logging.Formatter(
                fmt="[%(process)d] %(levelname)s %(user_tag)s%(name)s:%(lineno)d: %(message)s"
            )

            record = logging.LogRecord(
                "test.logger", logging.WARNING, "", 256, "警告消息", (), None
            )
            record.user_tag = ""  # 空标签

            output = formatter.format(record)

            # 验证无多余空格（不会出现"WARNING  test.logger"的情况）
            assert output == "[12345] WARNING test.logger:256: 警告消息"
            assert not output.startswith("[12345] WARNING  ")  # 确保没有双空格

    @staticmethod
    def test_formatter_timestamp_format():
        """测试完整的时间戳格式包含user_tag、进程ID和行号"""
        with patch('os.getpid', return_value=12345):
            formatter = logging.Formatter(
                fmt="%(asctime)s.%(msecs)03d [%(process)d] %(levelname)s %(user_tag)s%(name)s:%(lineno)d: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )

            record = logging.LogRecord(
                "test.logger", logging.INFO, "", 512, "消息", (), None
            )
            record.user_tag = "[USER] "

            output = formatter.format(record)

            # 验证完整格式（包含进程ID和行号）
            assert "[USER] test.logger:512: 消息" in output
            assert "INFO" in output
            assert "[12345]" in output
            # 验证时间戳格式正确（YYYY-MM-DD HH:MM:SS.mmm格式）
            assert "2026-" in output or "2025-" in output  # 年份部分
            assert "." in output  # 毫秒分隔符存在

    @staticmethod
    def test_formatter_handles_different_log_levels():
        """测试formatter处理不同日志级别（包含进程ID和行号）"""
        with patch('os.getpid', return_value=12345):
            formatter = logging.Formatter(
                fmt="[%(process)d] %(levelname)s %(user_tag)s%(name)s:%(lineno)d: %(message)s"
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
                    "test.logger", level, "", 88, "消息", (), None
                )
                record.user_tag = "[USER] "

                output = formatter.format(record)

                # 验证级别名称正确（包含进程ID和行号）
                assert output.startswith("[12345] " + level_name)
                # 验证tag和lineno存在
                assert "[USER] test.logger:88: 消息" in output