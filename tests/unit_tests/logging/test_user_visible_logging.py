"""单元测试：UserVisibleFormatter 功能验证"""

import logging
import re
import pytest
from jiuwenclaw.logging import UserVisibleFormatter


# 生产环境格式配置
PROD_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s"
PROD_DATEFMT = "%Y-%m-%d %H:%M:%S"

# 用于验证 Tag 位置的正则表达式
# 格式: YYYY-MM-DD HH:MM:SS.mmm LEVEL [USER] logger.name: message
TAG_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}\s\w+\s\[USER\]\s')
NO_TAG_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}\s\w+\s')


def create_formatter(fmt=PROD_FORMAT, datefmt=PROD_DATEFMT):
    """创建 UserVisibleFormatter 的辅助函数。"""
    return UserVisibleFormatter(fmt=fmt, datefmt=datefmt)


def create_record(name="test.logger", level=logging.INFO, msg="测试消息", user_visible=None):
    """创建 LogRecord 的辅助函数。"""
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="test.py",
        lineno=1,
        msg=msg,
        args=(),
        exc_info=None,
    )
    if user_visible is not None:
        record.user_visible = user_visible
    return record


def test_user_visible_formatter_with_tag():
    """测试 UserVisibleFormatter 为用户可见日志添加 [USER] Tag。"""
    formatter = create_formatter()
    record = create_record(msg="创建笔记: 会议记录", user_visible='critical')
    result = formatter.format(record)

    # 验证 [USER] Tag 出现在正确位置（时间戳和日志级别之后）
    assert "[USER]" in result
    # 验证格式：时间戳 日志级别 [USER] logger.name: message
    assert result.startswith(("2026-", "2025-", "2024-"))  # 年份开头
    assert " INFO [USER] test.logger: 创建笔记: 会议记录" in result


def test_user_visible_formatter_without_tag():
    """测试 UserVisibleFormatter 不为普通日志添加 [USER] Tag。"""
    formatter = create_formatter()
    record = create_record(msg="系统启动中...")
    result = formatter.format(record)

    # 验证不包含 [USER] Tag
    assert "[USER]" not in result
    # 验证格式：时间戳 日志级别 logger.name: message
    assert result.startswith(("2026-", "2025-", "2024-"))
    assert " INFO test.logger: 系统启动中..." in result


def test_user_visible_formatter_with_false_attribute():
    """测试 user_visible=False 时不添加 Tag。"""
    formatter = create_formatter()
    record = create_record(msg="技术性错误日志", user_visible=False)
    result = formatter.format(record)

    assert "[USER]" not in result
    assert " INFO test.logger: 技术性错误日志" in result


def test_user_visible_formatter_missing_attribute():
    """测试缺少 user_visible 属性时的默认行为。"""
    formatter = create_formatter()
    # 不设置 user_visible 属性
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="默认日志",
        args=(),
        exc_info=None,
    )
    result = formatter.format(record)

    # 默认行为：不添加 [USER] Tag
    assert "[USER]" not in result
    assert " INFO test.logger: 默认日志" in result


def test_tag_position_validation():
    """测试 [USER] Tag 出现在正确的位置（日志级别之后，logger 名称之前）。"""
    formatter = create_formatter()
    record = create_record(user_visible='critical')
    result = formatter.format(record)

    # 使用正则表达式验证 Tag 位置
    match = TAG_PATTERN.match(result)
    assert match is not None, f"Tag 位置不正确: {result}"

    # 验证 logger.name 在 [USER] 之后
    tag_pos = result.find("[USER]")
    logger_pos = result.find("test.logger:")
    assert tag_pos < logger_pos, f"Tag 应该在 logger 名称之前: {result}"


def test_different_log_levels():
    """测试不同日志级别的兼容性。"""
    formatter = create_formatter()

    levels = [
        (logging.DEBUG, "DEBUG"),
        (logging.INFO, "INFO"),
        (logging.WARNING, "WARNING"),
        (logging.ERROR, "ERROR"),
        (logging.CRITICAL, "CRITICAL"),
    ]

    for level, level_name in levels:
        record = create_record(level=level, msg=f"{level_name} 消息", user_visible='critical')
        result = formatter.format(record)

        assert f" {level_name} [USER] test.logger: {level_name} 消息" in result, \
            f"日志级别 {level_name} 测试失败: {result}"


def test_special_characters_in_message():
    """测试特殊字符日志消息不影响 Tag 位置。"""
    formatter = create_formatter()
    special_messages = [
        "消息包含: 冒号",
        "消息包含 [方括号]",
        "消息包含 {花括号}",
        "消息包含 '引号'",
        "消息包含 \"双引号\"",
        "消息包含 /斜杠\\ 反斜杠",
        "消息包含制表符\t",
        "🎉 消息包含 emoji 表情",
        "消息包含@特殊#符号$",
        "消息包含%百分^号&",
        "消息包含*星号(括号)",
    ]

    for msg in special_messages:
        record = create_record(msg=msg, user_visible='critical')
        result = formatter.format(record)

        # 验证 Tag 在正确位置
        assert "[USER]" in result, f"消息: {msg}, 结果: {result}"
        # 验证 logger.name 在 [USER] 之后
        tag_pos = result.find("[USER]")
        logger_pos = result.find("test.logger:")
        assert tag_pos < logger_pos, f"Tag 位置错误: {result}"


def test_user_visible_false_no_attribute_difference():
    """测试 user_visible=False 和缺少属性的行为一致性。"""
    formatter = create_formatter()

    # user_visible=False
    record_false = create_record(msg="测试", user_visible=False)
    result_false = formatter.format(record_false)

    # 缺少 user_visible 属性
    record_no_attr = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="测试",
        args=(),
        exc_info=None,
    )
    result_no_attr = formatter.format(record_no_attr)

    # 两者都不应该有 [USER] Tag
    assert "[USER]" not in result_false
    assert "[USER]" not in result_no_attr


def test_multiple_formatter_instances():
    """测试多个 Formatter 实例互不影响。"""
    formatter1 = create_formatter()
    formatter2 = create_formatter()

    record1 = create_record(msg="消息1", user_visible='critical')
    record2 = create_record(msg="消息2", user_visible=False)

    result1 = formatter1.format(record1)
    result2 = formatter2.format(record2)

    assert "[USER]" in result1
    assert "[USER]" not in result2


def test_formatter_with_different_logger_names():
    """测试不同 logger 名称的兼容性。"""
    formatter = create_formatter()

    logger_names = [
        "test.logger",
        "jiuwenclaw.gateway",
        "jiuwenclaw.channel.feishu",
        "jiuwenclaw.agentserver.react_agent",
    ]

    for logger_name in logger_names:
        record = create_record(name=logger_name, msg="测试", user_visible='critical')
        result = formatter.format(record)

        assert "[USER]" in result
        assert logger_name in result


# ===== v1.3 新增测试用例：分级 Tag 体系 =====

# 用于验证 [USER_PROGRESS] Tag 位置的正则表达式
PROGRESS_TAG_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3}\s\w+\s\[USER_PROGRESS\]\s')


def test_user_visible_with_critical_tag():
    """测试 user_visible='critical' 产生 [USER] Tag（v1.3）。"""
    formatter = create_formatter()
    record = create_record(msg="关键用户操作", user_visible='critical')
    result = formatter.format(record)

    assert "[USER]" in result
    assert "[USER_PROGRESS]" not in result
    assert " INFO [USER] test.logger: 关键用户操作" in result


def test_user_visible_with_progress_tag():
    """测试 user_visible='progress' 产生 [USER_PROGRESS] Tag（v1.3）。"""
    formatter = create_formatter()
    record = create_record(msg="进度信息", user_visible='progress')
    result = formatter.format(record)

    assert "[USER_PROGRESS]" in result
    assert "[USER]" not in result
    # 验证 Tag 在正确位置
    assert PROGRESS_TAG_PATTERN.match(result) is not None


def test_user_visible_with_false_no_tag():
    """测试 user_visible=False 不产生任何 Tag（v1.3）。"""
    formatter = create_formatter()
    record = create_record(msg="技术日志", user_visible=False)
    result = formatter.format(record)

    assert "[USER]" not in result
    assert "[USER_PROGRESS]" not in result


def test_user_visible_with_string_critical():
    """测试直接使用字符串 'critical' 产生 [USER] Tag（v1.3）。"""
    formatter = create_formatter()
    record = create_record(msg="关键操作", user_visible='critical')
    result = formatter.format(record)

    assert "[USER]" in result
    assert " INFO [USER] test.logger: 关键操作" in result


def test_user_visible_with_unknown_value_no_tag():
    """测试未知的 user_visible 值不产生 Tag（v1.3）。"""
    formatter = create_formatter()
    # 创建一个 LogRecord 并设置未知值
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="未知值测试",
        args=(),
        exc_info=None,
    )
    record.user_visible = 'unknown_value'
    result = formatter.format(record)

    # 未知值不应该产生任何 Tag
    assert "[USER]" not in result
    assert "[USER_PROGRESS]" not in result


def test_progress_tag_position():
    """测试 [USER_PROGRESS] Tag 出现在正确的位置（日志级别之后，logger 名称之前）。"""
    formatter = create_formatter()
    record = create_record(msg="进度测试", user_visible='progress')
    result = formatter.format(record)

    # 使用正则表达式验证 Tag 位置
    match = PROGRESS_TAG_PATTERN.match(result)
    assert match is not None, f"Tag 位置不正确: {result}"

    # 验证 logger.name 在 [USER_PROGRESS] 之后
    tag_pos = result.find("[USER_PROGRESS]")
    logger_pos = result.find("test.logger:")
    assert tag_pos < logger_pos, f"Tag 应该在 logger 名称之前: {result}"


def test_critical_vs_progress_distinction():
    """测试 'critical' 和 'progress' 产生不同的 Tag（v1.3）。"""
    formatter = create_formatter()

    # 测试 'critical'
    record_critical = create_record(msg="关键操作", user_visible='critical')
    result_critical = formatter.format(record_critical)
    assert "[USER]" in result_critical
    assert "[USER_PROGRESS]" not in result_critical

    # 测试 'progress'
    record_progress = create_record(msg="进度信息", user_visible='progress')
    result_progress = formatter.format(record_progress)
    assert "[USER_PROGRESS]" in result_progress
    assert "[USER]" not in result_progress


def test_all_log_levels_with_critical_and_progress():
    """测试不同日志级别与两种 Tag 值的兼容性（v1.3）。"""
    formatter = create_formatter()

    levels = [
        (logging.DEBUG, "DEBUG"),
        (logging.INFO, "INFO"),
        (logging.WARNING, "WARNING"),
        (logging.ERROR, "ERROR"),
        (logging.CRITICAL, "CRITICAL"),
    ]

    for level, level_name in levels:
        # 测试 'critical'
        record_critical = create_record(level=level, msg=f"{level_name} 关键消息", user_visible='critical')
        result_critical = formatter.format(record_critical)
        assert f" {level_name} [USER] test.logger: {level_name} 关键消息" in result_critical

        # 测试 'progress'
        record_progress = create_record(level=level, msg=f"{level_name} 进度消息", user_visible='progress')
        result_progress = formatter.format(record_progress)
        assert f" {level_name} [USER_PROGRESS] test.logger: {level_name} 进度消息" in result_progress
