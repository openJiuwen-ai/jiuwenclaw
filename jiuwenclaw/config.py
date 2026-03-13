# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML

from jiuwenclaw.utils import get_config_file


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
            default = match.group(2) if match.group(2) is not None else ""
            return os.getenv(var_name, default)

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


def update_browser_in_config(updates: dict[str, Any]) -> None:
    """只更新 browser 段（如 chrome_path）并写回。"""
    data = _load_yaml_round_trip(_CONFIG_YAML_PATH)
    if "browser" not in data:
        data["browser"] = {}
    section = data["browser"]
    for k, v in updates.items():
        section[k] = v
    _dump_yaml_round_trip(_CONFIG_YAML_PATH, data)