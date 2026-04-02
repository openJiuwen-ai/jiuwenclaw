# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""用户可见日志Tag配置模块

允许用户通过配置文件和环境变量控制日志中 [USER] 和 [USER_PROGRESS] 两类Tag的显示。
"""

import os
import logging
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field

from ruamel.yaml import YAML


@dataclass
class LoggingTagConfig:
    """日志Tag配置

    控制用户可见日志Tag的启用/禁用。

    Attributes:
        user_visible: 是否启用 [USER] Tag（默认True）
        user_progress_visible: 是否启用 [USER_PROGRESS] Tag（默认True）
        _config_file_path: 配置文件路径（可选，用于热更新监听）
        _skip_env_load: 是否跳过环境变量和配置文件加载（用于测试）
    """
    user_visible: bool = True
    user_progress_visible: bool = True
    _config_file_path: Optional[Path] = None
    _env_prefix: str = "JIUWENCLAW_LOG_"
    _skip_env_load: bool = False

    def __post_init__(self):
        """初始化后加载配置"""
        # 如果跳过环境变量加载，直接返回
        if self._skip_env_load:
            return

        # 如果配置文件路径未指定，使用默认路径
        if self._config_file_path is None:
            self._config_file_path = Path.home() / ".jiuwenclaw" / "config" / "config.yaml"

        # 加载配置（优先级：环境变量 > config.yaml > 默认值）
        self._load_config()

    def _load_config(self):
        """加载配置

        配置优先级：
        1. 环境变量（最高优先级）
        2. config.yaml
        3. 默认值（最低优先级）
        """
        # 1. 从环境变量加载（优先级最高）
        user_visible = self._load_from_env("USER_VISIBLE", self.user_visible)
        user_progress_visible = self._load_from_env("USER_PROGRESS_VISIBLE", self.user_progress_visible)

        # 2. 如果环境变量未设置(None)，从config.yaml加载
        env_user_visible = os.getenv(f"{self._env_prefix}USER_VISIBLE")
        env_user_progress_visible = os.getenv(f"{self._env_prefix}USER_PROGRESS_VISIBLE")
        
        if env_user_visible is None:
            user_visible = self._load_from_yaml("user_visible", user_visible)
        if env_user_progress_visible is None:
            user_progress_visible = self._load_from_yaml("user_progress_visible", user_progress_visible)

        self.user_visible = user_visible
        self.user_progress_visible = user_progress_visible

    def _load_from_yaml(self, key: str, default: bool) -> bool:
        """从config.yaml加载配置

        Args:
            key: 配置键名（如 "user_visible"）
            default: 默认值

        Returns:
            bool: 配置值，如果配置文件不存在或该键未设置则返回默认值
        """
        try:
            if not self._config_file_path.exists():
                return default

            # ruamel.yaml 的 load() 默认是安全的（不会构建任意 Python 对象）
            # 注意：与 PyYAML 不同，ruamel.yaml 没有 safe_load() 方法
            yaml = YAML()
            with open(self._config_file_path, "r", encoding="utf-8") as f:
                content = f.read()
            data = yaml.load(content) or {}

            # 解析 logging.tags 段
            logging_config = data.get("logging", {})
            tags_config = logging_config.get("tags", {})

            # 获取配置值
            if key in tags_config:
                value = tags_config[key]
                if isinstance(value, bool):
                    return value

                # 支持 "true"/"false"/"1"/"0"/"yes"/"no"/"on"/"off" (不区分大小写)
                if isinstance(value, str):
                    return self.parse_bool_string(value, default)

            return default
        except Exception as e:
            logging.warning(f"Failed to load logging config from {self._config_file_path}: {e}")
            return default

    def _load_from_env(self, suffix: str, default: bool) -> bool:
        """从环境变量加载配置

        Args:
            suffix: 环境变量后缀（如 "USER_VISIBLE"）
            default: 默认值

        Returns:
            bool: 配置值，如果环境变量未设置则返回默认值
        """
        env_var = f"{self._env_prefix}{suffix}"
        value = os.getenv(env_var)
        if value is None:
            return default

        return self.parse_bool_string(value, default)

    @staticmethod
    def parse_bool_string(value: str, default: bool) -> bool:
        """解析布尔值字符串

        支持多种常见的布尔值字符串格式，不区分大小写。

        支持的格式：
        - "true"/"false"
        - "1"/"0"
        - "yes"/"no"
        - "on"/"off"
        - "y"/"n"

        Args:
            value: 要解析的字符串。如果不是字符串类型，直接返回默认值。
            default: 解析失败时的默认值。

        Returns:
            bool: 解析后的布尔值。如果 value 不在支持的格式列表中，
                  返回 default 并记录警告日志。

        Example:
            >>> LoggingTagConfig.parse_bool_string("yes", False)
            True
            >>> LoggingTagConfig.parse_bool_string("0", True)
            False
            >>> LoggingTagConfig.parse_bool_string("invalid", True)
            True  # 返回默认值，并记录警告

        Note:
            这是一个静态方法，不需要实例化 LoggingTagConfig 即可使用：

            >>> from jiuwenclaw.logging.config import LoggingTagConfig
            >>> enabled = LoggingTagConfig.parse_bool_string("on", False)
        """
        if not isinstance(value, str):
            return default

        value = value.strip().strip('"\'')
        value_lower = value.lower()
        
        # True值
        if value_lower in ("true", "1", "yes", "on", "y", "t"):
            return True
        # False值
        if value_lower in ("false", "0", "no", "off", "n", "f"):
            return False

        # 解析失败，返回默认值
        logging.warning(f"Failed to parse boolean value: '{value}', using default: {default}")
        return default

    def reload(self):
        """重新加载配置（支持热更新）

        从配置文件和环境变量重新加载配置。
        """
        if self._skip_env_load:
            # 测试模式下重置到默认值
            self.user_visible = True
            self.user_progress_visible = True
            return
        self._load_config()

    def is_user_visible_enabled(self) -> bool:
        """检查 [USER] Tag是否启用

        Returns:
            bool: True表示启用，False表示禁用
        """
        return self.user_visible

    def is_user_progress_visible_enabled(self) -> bool:
        """检查 [USER_PROGRESS] Tag是否启用

        Returns:
            bool: True表示启用，False表示禁用
        """
        return self.user_progress_visible

    def __str__(self) -> str:
        """字符串表示"""
        return (
            f"LoggingTagConfig("
            f"user_visible={self.user_visible}, "
            f"user_progress_visible={self.user_progress_visible})"
        )

    def __repr__(self) -> str:
        return self.__str__()


# 全局配置单例
_global_logging_tag_config: Optional[LoggingTagConfig] = None


def get_logging_tag_config() -> LoggingTagConfig:
    """获取全局日志Tag配置单例

    Returns:
        LoggingTagConfig: 全局配置对象
    """
    global _global_logging_tag_config
    if _global_logging_tag_config is None:
        _global_logging_tag_config = LoggingTagConfig()
    return _global_logging_tag_config


def reload_logging_tag_config() -> LoggingTagConfig:
    """重新加载全局日志Tag配置

    Returns:
        LoggingTagConfig: 重新加载后的全局配置对象
    """
    global _global_logging_tag_config
    _global_logging_tag_config = LoggingTagConfig()
    return _global_logging_tag_config
