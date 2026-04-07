# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""用户可见日志格式化器 - 配置化版本

支持通过配置控制 [USER] 和 [USER_PROGRESS] 两类Tag的显示。
"""

import logging
import re
from typing import Optional

from jiuwenclaw.jiuwenclaw_logging.config import LoggingTagConfig, get_logging_tag_config


class UserVisibleFormatter(logging.Formatter):
    """为用户可见日志添加分级 Tag 的自定义格式化器（配置化版本）

    支持通过 LoggingTagConfig 控制以下Tag的显示：
    - [USER] - 用户可见Tag（默认启用）
    - [USER_PROGRESS] - 用户进度可见Tag（默认启用）

    配置方式：
    1. config.yaml: logging.tags.user_visible / user_progress_visible
    2. 环境变量: JIUWENCLAW_LOG_USER_VISIBLE / JIUWENCLAW_LOG_USER_PROGRESS_VISIBLE

    配置优先级：环境变量 > config.yaml > 默认值(true)
    """

    _USER_TAG = "[USER]"
    _USER_PROGRESS_TAG = "[USER_PROGRESS]"
    _USER_VISIBLE_ATTR = "user_visible"

    # Tag 值定义（v1.3）
    _TAG_VALUE_CRITICAL = 'critical'  # 产生 [USER] 标签
    _TAG_VALUE_PROGRESS = 'progress'  # 产生 [USER_PROGRESS] 标签

    # 匹配格式: "YYYY-MM-DD HH:MM:SS.mmm LEVEL logger: message"
    # 捕获组: (完整时间戳) (空格) (级别) (空格) (其余部分)
    _LOG_PATTERN = re.compile(r'^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}\.\d{3})\s(\w+)\s(.+)$')

    def __init__(
        self,
        fmt: Optional[str] = None,
        datefmt: Optional[str] = None,
        style: str = '%',
        validate: bool = True,
        tag_config: Optional[LoggingTagConfig] = None,
    ):
        """初始化格式化器

        Args:
            fmt: 日志格式字符串
            datefmt: 日期格式字符串
            style: 格式化风格（%/{/$）
            validate: 是否验证格式
            tag_config: Tag配置对象，如果为None则使用全局配置
        """
        super().__init__(fmt, datefmt, style, validate)
        # 使用传入的配置或全局配置
        self.tag_config = tag_config or get_logging_tag_config()

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录，根据配置决定是否添加Tag

        Args:
            record: 日志记录对象

        Returns:
            str: 格式化后的日志字符串
        """
        # 先使用父类方法进行标准格式化
        result = super().format(record)

        # 获取 user_visible 属性
        user_visible = getattr(record, self._USER_VISIBLE_ATTR, None)

        # 处理 None（无标记）
        if user_visible is None:
            return result

        # 根据配置决定是否添加Tag
        # 如果配置禁用了所有Tag，直接返回
        if not self.tag_config.is_user_visible_enabled() and not self.tag_config.is_user_progress_visible_enabled():
            return result

        # 直接比较字符串值，确定要添加的 Tag
        if user_visible == self._TAG_VALUE_CRITICAL:
            tag = self._USER_TAG  # [USER]
        elif user_visible == self._TAG_VALUE_PROGRESS:
            tag = self._USER_PROGRESS_TAG  # [USER_PROGRESS]
        else:
            # 未知的值（包括布尔值），不添加 Tag
            return result

        # 检查配置是否允许添加该Tag
        if tag == self._USER_TAG and not self.tag_config.is_user_visible_enabled():
            return result
        if tag == self._USER_PROGRESS_TAG and not self.tag_config.is_user_progress_visible_enabled():
            return result

        # 使用正则在日志级别后插入 Tag
        match = self._LOG_PATTERN.match(result)
        if match:
            timestamp, level, rest = match.groups()
            result = f"{timestamp} {level} {tag} {rest}"

        return result
