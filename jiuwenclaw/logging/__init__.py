# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuWenClaw 日志模块

提供日志配置、格式化、处理和级别管理功能。

主要组件：
- config: 日志Tag配置（LoggingTagConfig、get_logging_tag_config）
- formatter: 用户可见日志格式化器（UserVisibleFormatter）
- handler: 日志文件轮转处理器（SafeRotatingFileHandler）
- levels: 日志级别管理（LoggingLevels、解析函数、组件过滤器）
- setup: 日志系统配置（setup_logger）
"""

from jiuwenclaw.logging.config import LoggingTagConfig, get_logging_tag_config
from jiuwenclaw.logging.formatter import UserVisibleFormatter
from jiuwenclaw.logging.handler import SafeRotatingFileHandler
from jiuwenclaw.logging.levels import (
    LoggingLevels,
    _ComponentNameFilter,
    _load_logging_config_from_yaml,
    _log_component_from_logger_name,
    _parse_log_level,
    _resolve_logging_levels,
)

__all__ = [
    # config.py
    "LoggingTagConfig",
    "get_logging_tag_config",
    # formatter.py
    "UserVisibleFormatter",
    # handler.py
    "SafeRotatingFileHandler",
    # levels.py
    "LoggingLevels",
    "_ComponentNameFilter",
    "_load_logging_config_from_yaml",
    "_log_component_from_logger_name",
    "_parse_log_level",
    "_resolve_logging_levels",
    # setup.py
    "setup_logger",
]


def __getattr__(name: str):
    """延迟导入 setup_logger 以避免循环依赖。"""
    if name == "setup_logger":
        from jiuwenclaw.logging.setup import setup_logger
        return setup_logger
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
