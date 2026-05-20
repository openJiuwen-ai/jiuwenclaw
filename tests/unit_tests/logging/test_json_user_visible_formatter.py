"""JSON Formatter格式单元测试"""

import logging
import json
from unittest.mock import patch

from jiuwenclaw.utils import JsonUserVisibleFormatter


class TestJsonUserVisibleFormatter:
    """测试JsonUserVisibleFormatter的输出格式和字段处理"""

    @staticmethod
    def test_json_formatter_with_process_id():
        """测试JSON格式始终包含process字段和lineno字段"""
        with patch('os.getpid', return_value=12345):
            # 创建JSON formatter（无需传递include_process_id参数）
            formatter = JsonUserVisibleFormatter()

            # 创建record并设置属性（lineno=88）
            record = logging.LogRecord(
                "test.logger", logging.INFO, "", 88, "测试消息", (), None
            )
            record.user_visible = "[USER] "

            # 格式化输出
            output = formatter.format(record)

            # 解析JSON
            json_obj = json.loads(output)

            # 验证process字段存在且为整数
            assert "process" in json_obj
            assert json_obj["process"] == 12345
            assert isinstance(json_obj["process"], int)

            # 验证lineno字段存在且为整数
            assert "lineno" in json_obj
            assert json_obj["lineno"] == 88
            assert isinstance(json_obj["lineno"], int)

    @staticmethod
    def test_json_formatter_field_order():
        """测试字段顺序：timestamp → process → level → user_tag → logger → lineno → message"""
        with patch('os.getpid', return_value=12345):
            formatter = JsonUserVisibleFormatter()

            record = logging.LogRecord(
                "test.logger", logging.INFO, "", 125, "测试消息", (), None
            )
            record.user_visible = "[USER] "

            output = formatter.format(record)
            json_obj = json.loads(output)

            # 验证关键字段存在
            assert "timestamp" in json_obj
            assert "process" in json_obj
            assert "level" in json_obj
            assert "logger" in json_obj
            assert "lineno" in json_obj
            assert "message" in json_obj

            # 获取字段顺序（从JSON字符串中检查）
            # 字段顺序在JSON字符串中应体现
            assert '"timestamp"' in output
            assert '"process"' in output
            assert '"level"' in output
            assert '"logger"' in output
            assert '"lineno"' in output
            assert '"message"' in output

            # 验证字段顺序：timestamp → process → level → logger → lineno → message
            timestamp_pos = output.find('"timestamp"')
            process_pos = output.find('"process"')
            level_pos = output.find('"level"')
            logger_pos = output.find('"logger"')
            lineno_pos = output.find('"lineno"')
            message_pos = output.find('"message"')

            assert timestamp_pos < process_pos < level_pos < logger_pos < lineno_pos < message_pos

    @staticmethod
    def test_json_formatter_with_user_visible():
        """测试JSON格式包含user_visible字段、user_tag字段和lineno字段"""
        from jiuwenclaw.utils import UserVisibleTagFilter

        # 创建filter和formatter
        filter_obj = UserVisibleTagFilter()
        formatter = JsonUserVisibleFormatter()

        # 创建record并设置正确的user_visible属性（lineno=1024）
        record = logging.LogRecord(
            "test.logger", logging.INFO, "", 1024, "测试消息", (), None
        )
        record.user_visible = 'critical'  # 设置正确的值

        # 应用filter来设置user_tag属性
        filter_obj.filter(record)

        # 格式化输出
        output = formatter.format(record)
        json_obj = json.loads(output)

        # 验证user_visible字段
        assert "user_visible" in json_obj
        assert json_obj["user_visible"] == 'critical'

        # 验证user_tag字段（由filter添加）
        assert "user_tag" in json_obj
        assert json_obj["user_tag"] == "[USER] "

        # 验证lineno字段
        assert "lineno" in json_obj
        assert json_obj["lineno"] == 1024

    @staticmethod
    def test_json_formatter_component_field():
        """测试JSON格式自动推导component字段和lineno字段"""
        formatter = JsonUserVisibleFormatter()

        record = logging.LogRecord(
            "jiuwenclaw.gateway.channel_manager", logging.INFO, "", 256,
            "已派发到 Channel", (), None
        )
        record.user_visible = None

        output = formatter.format(record)
        json_obj = json.loads(output)

        # 验证component字段
        assert "component" in json_obj
        # component应从logger name推导

        # 验证lineno字段
        assert "lineno" in json_obj
        assert json_obj["lineno"] == 256