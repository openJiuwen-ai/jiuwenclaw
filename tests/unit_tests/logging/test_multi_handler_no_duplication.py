"""多handler场景测试 - 验证无重复标签"""

import logging
from io import StringIO

import pytest

from jiuwenclaw.utils import UserVisibleTagFilter, LoggingTagConfig


class TestMultiHandlerNoDuplication:
    """测试多个handler读取同一个record.user_tag，无重复标签"""

    def test_multiple_handlers_read_same_user_tag_field(self):
        """测试多个handler读取同一个record.user_tag字段，无重复添加"""
        # 创建配置
        config = LoggingTagConfig()
        config.user_visible = True

        # 创建formatter（使用新格式）
        formatter = logging.Formatter(
            fmt="%(levelname)s %(user_tag)s%(name)s: %(message)s"
        )

        # 创建多个handler（模拟gateway.log、full.log、stream）
        handlers = []
        streams = []
        for i in range(3):
            stream = StringIO()
            handler = logging.StreamHandler(stream)
            handler.setFormatter(formatter)
            handler.addFilter(UserVisibleTagFilter(config))
            handlers.append(handler)
            streams.append(stream)

        # 创建logger并添加所有handler
        logger = logging.getLogger("test_multi_handler")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        for handler in handlers:
            logger.addHandler(handler)

        # 发送带user_visible的日志
        logger.info("测试消息", extra={'user_visible': 'critical'})

        # 验证每个handler的输出都只有一个[USER]标签
        for i, stream in enumerate(streams):
            output = stream.getvalue()
            assert output.count("[USER]") == 1, f"Handler {i} 应该只有一个[USER]标签，但找到 {output.count('[USER]')} 个"
            assert "INFO [USER] test_multi_handler: 测试消息" in output

    def test_different_handlers_show_consistent_tags(self):
        """测试不同handler显示一致的tag（不是重复累加）"""
        config = LoggingTagConfig()
        config.user_visible = True
        config.user_progress_visible = True

        formatter = logging.Formatter(
            fmt="%(levelname)s %(user_tag)s%(name)s: %(message)s"
        )

        # 创建3个handler
        handlers = []
        streams = []
        for i in range(3):
            stream = StringIO()
            handler = logging.StreamHandler(stream)
            handler.setFormatter(formatter)
            handler.addFilter(UserVisibleTagFilter(config))
            handlers.append(handler)
            streams.append(stream)

        logger = logging.getLogger("test_consistency")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        for handler in handlers:
            logger.addHandler(handler)

        # 发送进度消息
        logger.info("进度消息", extra={'user_visible': 'progress'})

        # 验证所有handler的输出一致
        outputs = [stream.getvalue() for stream in streams]
        for i, output in enumerate(outputs):
            assert "INFO [USER_PROGRESS] test_consistency: 进度消息" in output

        # 验证所有输出完全相同
        assert len(set(outputs)) == 1, "所有handler的输出应该完全相同"

    def test_no_duplication_across_handler_types(self):
        """测试不同类型的handler（文件、控制台等）无重复标签"""
        config = LoggingTagConfig()
        config.user_visible = True

        formatter = logging.Formatter(
            fmt="%(levelname)s %(user_tag)s%(name)s: %(message)s"
        )

        # 创建不同类型的handler
        stream_handler = logging.StreamHandler(StringIO())
        file_handler = logging.StreamHandler(StringIO())  # 模拟文件handler

        for handler in [stream_handler, file_handler]:
            handler.setFormatter(formatter)
            handler.addFilter(UserVisibleTagFilter(config))

        logger = logging.getLogger("test_handler_types")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.addHandler(stream_handler)
        logger.addHandler(file_handler)

        # 发送关键操作消息
        logger.info("关键操作", extra={'user_visible': 'critical'})

        # 验证两个handler都只有一个标签
        stream_output = stream_handler.stream.getvalue()
        file_output = file_handler.stream.getvalue()

        assert stream_output.count("[USER]") == 1
        assert file_output.count("[USER]") == 1

    def test_filter_only_sets_user_tag_once_per_record(self):
        """测试filter每条记录只设置一次user_tag，不重复修改"""
        config = LoggingTagConfig()
        config.user_visible = True

        filter_obj = UserVisibleTagFilter(config)

        # 创建一个record
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "原始消息", (), None
        )
        record.user_visible = "critical"

        # 第一次调用filter
        result1 = filter_obj.filter(record)
        user_tag_after_first = record.user_tag

        # 第二次调用filter（模拟第二个handler处理）
        result2 = filter_obj.filter(record)
        user_tag_after_second = record.user_tag

        # 验证user_tag值没有变化
        assert result1 is True
        assert result2 is True
        assert user_tag_after_first == "[USER] "
        assert user_tag_after_second == "[USER] "
        assert user_tag_after_first == user_tag_after_second

    def test_original_message_unchanged_across_handlers(self):
        """测试原始消息内容在多个handler处理后保持不变"""
        config = LoggingTagConfig()
        config.user_visible = True

        formatter = logging.Formatter(
            fmt="%(levelname)s %(user_tag)s%(name)s: %(message)s"
        )

        handlers = []
        streams = []
        for i in range(3):
            stream = StringIO()
            handler = logging.StreamHandler(stream)
            handler.setFormatter(formatter)
            handler.addFilter(UserVisibleTagFilter(config))
            handlers.append(handler)
            streams.append(stream)

        logger = logging.getLogger("test_message_unchanged")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        for handler in handlers:
            logger.addHandler(handler)

        original_message = "这是原始消息内容"
        logger.info(original_message, extra={'user_visible': 'critical'})

        # 验证所有handler的消息内容都是原始消息
        for stream in streams:
            output = stream.getvalue()
            assert original_message in output
            # 验证没有重复的标签或消息内容
            assert output.count(original_message) == 1