# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""日志级别管理模块

提供日志级别配置、解析和组件过滤功能。
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ruamel.yaml import YAML

# 创建模块级别的 logger
logger = logging.getLogger(__name__)


@dataclass
class LoggingLevels:
    """日志级别配置容器。"""
    logger: int
    console: int
    gateway: int
    channel: int
    agent_server: int
    full: int


def _parse_log_level(name: str, default: int = logging.INFO) -> int:
    """解析日志级别名称为 logging 模块常量。

    Args:
        name: 日志级别名称（如 "INFO"、"DEBUG"）
        default: 默认日志级别

    Returns:
        int: 日志级别常量
    """
    if not name or not isinstance(name, str):
        return default
    return getattr(logging, name.strip().upper(), default)


def _log_component_from_logger_name(name: str) -> str:
    """按 ``logging.getLogger(__name__)`` 的 logger 名划分组件。

    Args:
        name: Logger 名称

    Returns:
        str: 组件名称（"gateway"、"channel" 或 "agent_server"）
    """
    if name.startswith("jiuwenclaw.channel"):
        return "channel"
    if name.startswith("jiuwenclaw.agentserver"):
        return "agent_server"
    return "gateway"


class _ComponentNameFilter(logging.Filter):
    """仅放行指定组件（由 logger 名判定）的日志记录。"""

    def __init__(self, component: str) -> None:
        """初始化过滤器。

        Args:
            component: 组件名称（"gateway"、"channel" 或 "agent_server"）
        """
        super().__init__()
        self.component = component

    def filter(self, record: logging.LogRecord) -> bool:
        """过滤日志记录。

        Args:
            record: 日志记录对象

        Returns:
            bool: 如果记录来自指定组件则返回 True，否则返回 False
        """
        return _log_component_from_logger_name(record.name) == self.component


def _load_logging_config_from_yaml() -> dict[str, Any]:
    """读取 ~/.jiuwenclaw/config/config.yaml 中的 logging 段（无则空）。

    Returns:
        dict: 日志配置字典，如果读取失败或不存在则返回空字典
    """
    try:
        # 延迟导入避免循环依赖
        from jiuwenclaw.utils import get_config_file

        cf = get_config_file()
        if not cf.exists():
            return {}
        rt = YAML()
        with open(cf, "r", encoding="utf-8") as f:
            data = rt.load(f) or {}
        raw = data.get("logging")
        if isinstance(raw, dict):
            return raw
    except Exception as e:
        logger.error(f"load logging config failed, caused by={e}")
    return {}


def _resolve_logging_levels(
    log_level_override: Optional[str],
) -> LoggingLevels:
    """返回日志级别配置。

    Args:
        log_level_override: 可选的日志级别覆盖值

    Returns:
        LoggingLevels: 日志级别配置对象
    """
    cfg = _load_logging_config_from_yaml()
    base = _parse_log_level(str(cfg.get("level", "INFO")))

    def _coerce(key: str) -> int:
        """从配置中获取日志级别，如果不存在则使用基础级别。"""
        if key in cfg and cfg[key] is not None:
            return _parse_log_level(str(cfg[key]), base)
        return base

    console = _coerce("console_level")
    env_console = os.getenv("LOG_LEVEL")
    if env_console:
        console = _parse_log_level(env_console, console)

    gateway = _coerce("gateway")
    channel = _coerce("channel")
    agent_server = _coerce("agent_server")
    full = _coerce("full")

    if log_level_override is not None:
        v = _parse_log_level(log_level_override)
        console = gateway = channel = agent_server = full = v
        logger_level = v
    else:
        logger_level = min(gateway, channel, agent_server, full)

    return LoggingLevels(logger_level, console, gateway, channel, agent_server, full)
