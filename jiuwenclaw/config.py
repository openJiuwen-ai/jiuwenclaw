# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML

from jiuwenclaw.utils import get_config_file
from jiuwenclaw.local_env_config import get_local_config

_CONFIG_MODULE_DIR = Path(__file__).parent
_CONFIG_YAML_PATH = get_config_file()

# Check if user workspace exists and use it if configured via env
_user_config = os.getenv("JIUWENCLAW_CONFIG_DIR")
if _user_config:
    _CONFIG_MODULE_DIR = Path(_user_config)
elif (Path.home() / ".jiuwenclaw" / "config").exists():
    _CONFIG_MODULE_DIR = Path.home() / ".jiuwenclaw" / "config"

# Ensure config directory is in sys.path
if str(_CONFIG_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_CONFIG_MODULE_DIR))


def resolve_env_vars(value: Any) -> Any:
    """递归解析配置中的环境变量替换语法 ${VAR:-default}.

    Args:
        value: 配置值，可能是字符串、字典或列表

    Returns:
        解析后的值
    """
    if isinstance(value, str):
        # 匹配 ${VAR:-default} 格式
        pattern = r'\$\{([^:}]+)(?::-([^}]*))?\}'

        def replace_env(match):
            var_name = match.group(1)
            default = match.group(2)
            current = get_local_config(var_name)
            # Bash: ${VAR:-default} uses default when VAR is unset OR empty.
            # ${VAR} (no :-) keeps getenv behavior; unset -> "".
            if default is not None:
                if current is None or current == "":
                    return default
                return current
            return current if current is not None else ""

        return re.sub(pattern, replace_env, value)
    elif isinstance(value, dict):
        return {k: resolve_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [resolve_env_vars(item) for item in value]
    else:
        return value


def get_config():
    with open(get_config_file(), "r", encoding="utf-8") as f:
        config_base = yaml.safe_load(f)
    config_base = resolve_env_vars(config_base)

    return config_base


def get_config_raw():
    """读 config.yaml 原始内容（不解析环境变量），供局部更新后写回。"""
    with open(_CONFIG_YAML_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def set_config(config):
    with open(_CONFIG_YAML_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)


def _load_yaml_round_trip(config_path: Path):
    """ruamel 加载 config，保留注释与格式。"""
    rt = YAML()
    rt.preserve_quotes = True
    with open(config_path, "r", encoding="utf-8") as f:
        return rt.load(f)


def _dump_yaml_round_trip(config_path: Path, data: Any) -> None:
    """ruamel 写回 config，保留注释与格式。"""
    rt = YAML()
    rt.preserve_quotes = True
    rt.default_flow_style = False
    # mapping 2 空格；list 用 sequence=4 + offset=2 保证 dash 前有 2 空格（tools: 下 - todo），否则 list 会变成无缩进
    rt.indent(mapping=2, sequence=4, offset=2)
    rt.width = 4096
    with open(config_path, "w", encoding="utf-8") as f:
        rt.dump(data, f)


def update_heartbeat_in_config(payload: dict[str, Any]) -> None:
    """只更新 heartbeat 段并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    if "heartbeat" not in data:
        data["heartbeat"] = {}
    hb = data["heartbeat"]
    if "every" in payload:
        hb["every"] = payload["every"]
    if "target" in payload:
        hb["target"] = payload["target"]
    if "active_hours" in payload:
        hb["active_hours"] = payload["active_hours"]
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)


def update_channel_in_config(channel_id: str, conf: dict[str, Any]) -> None:
    """只更新 channels[channel_id] 并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    if "channels" not in data:
        data["channels"] = {}
    channels = data["channels"]
    if channel_id not in channels:
        channels[channel_id] = {}
    section = channels[channel_id]
    for k, v in conf.items():
        section[k] = v
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)


def update_channel_subsection_in_config(
    channel_id: str,
    subsection_id: str,
    conf: dict[str, Any],
) -> None:
    """更新 channels[channel_id][subsection_id] 并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    if "channels" not in data:
        data["channels"] = {}
    channels = data["channels"]
    if channel_id not in channels:
        channels[channel_id] = {}
    section = channels[channel_id]
    if subsection_id not in section:
        section[subsection_id] = {}
    subsection = section[subsection_id]
    for k, v in conf.items():
        subsection[k] = v
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)


def update_preferred_language_in_config(lang: str) -> None:
    """只更新顶层 preferred_language 并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    data["preferred_language"] = lang
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)


def set_preferred_language_in_config_file(config_path: Path, lang: str) -> None:
    """将 preferred_language 写入指定 config.yaml（用于 init 等尚未绑定全局路径的场景）。"""
    lang = str(lang or "zh").strip().lower()
    if lang not in ("zh", "en"):
        lang = "zh"
    if not config_path.exists():
        return
    data = _load_yaml_round_trip(config_path)
    data["preferred_language"] = lang
    _dump_yaml_round_trip(config_path, data)


def update_browser_in_config(updates: dict[str, Any]) -> None:
    """只更新 browser 段（如 chrome_path）并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    if "browser" not in data:
        data["browser"] = {}
    section = data["browser"]
    for k, v in updates.items():
        section[k] = v
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)


def update_context_engine_enabled_in_config(value: bool) -> None:
    """更新 react.context_engine_config.enabled（上下文压缩开关）并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    if "react" not in data:
        data["react"] = {}
    react = data["react"]
    if "context_engine_config" not in react:
        react["context_engine_config"] = {}
    react["context_engine_config"]["enabled"] = value
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)


def update_permissions_enabled_in_config(value: bool) -> None:
    """更新 permissions.enabled（工具安全护栏开关）并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    if "permissions" not in data:
        data["permissions"] = {}
    data["permissions"]["enabled"] = value
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)


def update_updater_in_config(updates: dict[str, Any]) -> None:
    """只更新 updater 段并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    if "updater" not in data:
        data["updater"] = {}
    section = data["updater"]
    for key, value in updates.items():
        section[key] = value
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)


# ===========================================================================
# 文件传输配置
# ===========================================================================

@dataclass
class FileTransferConfig:
    """文件传输配置模型.

    Attributes:
        enabled: 是否启用分布式模式（false=本地模式，true=分布式模式）
        chunk_size: 分片大小（字节），默认 64KB
        max_file_size: 最大文件大小（字节），默认 100MB，0=不限制
        transfer_timeout: 传输超时时间（秒），默认 300 秒
        max_retries: 单分片最大重试次数，默认 3 次
        received_files_dir: 接收文件存储目录，默认 "agent/workspace/received_files"
        cleanup_interval: 临时文件清理间隔（秒），默认 3600 秒
        cleanup_age: 清理超过 N 秒的临时文件，默认 86400 秒（24小时）
        max_concurrent_transfers: 最大并发传输数，默认 5
    """
    enabled: bool = False
    chunk_size: int = 65536  # 64KB
    max_file_size: int = 104857600  # 100MB
    transfer_timeout: int = 300
    max_retries: int = 3
    received_files_dir: str = "agent/workspace/received_files"
    cleanup_interval: int = 3600
    cleanup_age: int = 86400
    max_concurrent_transfers: int = 5

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "FileTransferConfig":
        """从字典创建配置实例."""
        if not data:
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            chunk_size=int(data.get("chunk_size", 65536)),
            max_file_size=int(data.get("max_file_size", 104857600)),
            transfer_timeout=int(data.get("transfer_timeout", 300)),
            max_retries=int(data.get("max_retries", 3)),
            received_files_dir=str(data.get("received_files_dir", "agent/workspace/received_files")),
            cleanup_interval=int(data.get("cleanup_interval", 3600)),
            cleanup_age=int(data.get("cleanup_age", 86400)),
            max_concurrent_transfers=int(data.get("max_concurrent_transfers", 5)),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为字典."""
        return {
            "enabled": self.enabled,
            "chunk_size": self.chunk_size,
            "max_file_size": self.max_file_size,
            "transfer_timeout": self.transfer_timeout,
            "max_retries": self.max_retries,
            "received_files_dir": self.received_files_dir,
            "cleanup_interval": self.cleanup_interval,
            "cleanup_age": self.cleanup_age,
            "max_concurrent_transfers": self.max_concurrent_transfers,
        }


# 文件传输配置缓存
_file_transfer_config: FileTransferConfig | None = None


def get_file_transfer_config() -> FileTransferConfig:
    """获取文件传输配置（带缓存）."""
    global _file_transfer_config
    if _file_transfer_config is not None:
        return _file_transfer_config
    config = get_config()
    ft_config = config.get("file_transfer", {}) if isinstance(config, dict) else {}
    _file_transfer_config = FileTransferConfig.from_dict(ft_config)
    return _file_transfer_config


def clear_file_transfer_config_cache() -> None:
    """清除文件传输配置缓存."""
    global _file_transfer_config
    _file_transfer_config = None


def update_file_transfer_in_config(updates: dict[str, Any]) -> None:
    """更新 file_transfer 段并写回."""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    if "file_transfer" not in data:
        data["file_transfer"] = {}
    section = data["file_transfer"]
    for key, value in updates.items():
        section[key] = value
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)
    clear_file_transfer_config_cache()
