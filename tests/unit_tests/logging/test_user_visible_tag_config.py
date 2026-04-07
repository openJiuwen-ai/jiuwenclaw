# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""用户可见日志Tag配置测试"""

import os
import unittest
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from jiuwenclaw.jiuwenclaw_logging.config import LoggingTagConfig, get_logging_tag_config
from jiuwenclaw.jiuwenclaw_logging.formatter import UserVisibleFormatter


class TestLoggingTagConfig(unittest.TestCase):
    """测试 LoggingTagConfig 配置类"""

    @pytest.mark.filterwarnings("ignore::ResourceWarning")
    def test_default_config(self):
        """测试默认配置（两个Tag都启用）

        注意：此测试可能触发 CI 环境中的 ResourceWarning，
        该警告源于 ruamel.yaml 在 YAML 解析过程中的内部行为。
        因此在此测试中抑制 ResourceWarning。
        """
        config = LoggingTagConfig()
        self.assertTrue(config.is_user_visible_enabled())
        self.assertTrue(config.is_user_progress_visible_enabled())

    def test_config_from_dict(self):
        """测试从字典创建配置"""
        config = LoggingTagConfig(
            user_visible=False,
            user_progress_visible=True,
            _skip_env_load=True  # 跳过环境变量加载
        )
        self.assertFalse(config.is_user_visible_enabled())
        self.assertTrue(config.is_user_progress_visible_enabled())

    def test_env_override(self):
        """测试环境变量覆盖"""
        # 设置环境变量
        os.environ["JIUWENCLAW_LOG_USER_VISIBLE"] = "false"
        os.environ["JIUWENCLAW_LOG_USER_PROGRESS_VISIBLE"] = "false"

        try:
            config = LoggingTagConfig()
            self.assertFalse(config.is_user_visible_enabled())
            self.assertFalse(config.is_user_progress_visible_enabled())
        finally:
            # 清理环境变量
            del os.environ["JIUWENCLAW_LOG_USER_VISIBLE"]
            del os.environ["JIUWENCLAW_LOG_USER_PROGRESS_VISIBLE"]

    def test_env_override_with_yaml(self):
        """测试环境变量覆盖config.yaml"""
        with TemporaryDirectory() as tmpdir:
            # 创建config.yaml
            config_file = Path(tmpdir) / "config.yaml"
            config_file.write_text("""
logging:
  tags:
    user_visible: true
    user_progress_visible: false
""")

            # 设置环境变量覆盖
            os.environ["JIUWENCLAW_LOG_USER_VISIBLE"] = "false"

            try:
                config = LoggingTagConfig(_config_file_path=config_file)
                # 环境变量应该覆盖config.yaml
                self.assertFalse(config.is_user_visible_enabled())
                self.assertFalse(config.is_user_progress_visible_enabled())
            finally:
                # 清理环境变量
                del os.environ["JIUWENCLAW_LOG_USER_VISIBLE"]

    def test_boolean_parsing(self):
        """测试布尔值解析

        测试 LoggingTagConfig.parse_bool_string() 公共方法
        支持的各种输入格式。
        """
        test_cases = [
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("1", True),
            ("yes", True),
            ("Yes", True),
            ("YES", True),
            ("on", True),
            ("On", True),
            ("ON", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("0", False),
            ("no", False),
            ("No", False),
            ("NO", False),
            ("off", False),
            ("Off", False),
            ("OFF", False),
        ]

        for value, expected in test_cases:
            with self.subTest(value=value):
                # 使用公共静态方法，无需实例化
                result = LoggingTagConfig.parse_bool_string(value, default=False)
                self.assertEqual(result, expected)

    def test_reload(self):
        """测试配置重新加载"""
        config = LoggingTagConfig(
            _skip_env_load=True
        )
        self.assertTrue(config.is_user_visible_enabled())

        # 修改配置
        config.user_visible = False
        config.user_progress_visible = False
        config.reload()

        # 重新加载后应该恢复到默认值
        self.assertTrue(config.is_user_visible_enabled())
        self.assertTrue(config.is_user_progress_visible_enabled())


class TestUserVisibleFormatter(unittest.TestCase):
    """测试 UserVisibleFormatter 配置化版本"""

    def test_formatter_with_default_config(self):
        """测试使用默认配置的格式化器"""
        formatter = UserVisibleFormatter()
        self.assertTrue(formatter.tag_config.is_user_visible_enabled())
        self.assertTrue(formatter.tag_config.is_user_progress_visible_enabled())

    def test_formatter_with_custom_config(self):
        """测试使用自定义配置的格式化器"""
        config = LoggingTagConfig(
            user_visible=False,
            user_progress_visible=False,
            _skip_env_load=True  # 跳过环境变量加载
        )
        formatter = UserVisibleFormatter(tag_config=config)
        self.assertFalse(formatter.tag_config.is_user_visible_enabled())
        self.assertFalse(formatter.tag_config.is_user_progress_visible_enabled())

    def test_format_with_user_visible_enabled(self):
        """测试启用 [USER] Tag 的格式化"""
        config = LoggingTagConfig(
            user_visible=True,
            user_progress_visible=False,
            _skip_env_load=True  # 跳过环境变量加载
        )
        # 需要提供格式字符串，这样正则表达式才能匹配并添加 Tag
        fmt = "%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s"
        datefmt = "%Y-%m-%d %H:%M:%S"
        formatter = UserVisibleFormatter(fmt=fmt, datefmt=datefmt, tag_config=config)

        # 创建日志记录（需要设置 user_visible 属性）
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test message",
            args=(),
            exc_info=None
        )
        record.user_visible = 'critical'  # 添加 user_visible 属性

        # 格式化日志
        result = formatter.format(record)

        # 验证结果包含 [USER] Tag
        self.assertIn("[USER]", result)

    def test_format_with_user_visible_disabled(self):
        """测试禁用 [USER] Tag 的格式化"""
        config = LoggingTagConfig(
            user_visible=False,
            user_progress_visible=False,
            _skip_env_load=True  # 跳过环境变量加载
        )
        formatter = UserVisibleFormatter(tag_config=config)

        # 创建日志记录
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test message",
            args=(),
            exc_info=None
        )

        # 格式化日志
        result = formatter.format(record)

        # 验证结果不包含 [USER] Tag
        self.assertNotIn("[USER]", result)
        self.assertNotIn("[USER_PROGRESS]", result)


if __name__ == '__main__':
    unittest.main()
