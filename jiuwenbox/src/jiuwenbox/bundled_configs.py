# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Bundled policy YAML files shipped inside the jiuwenbox package."""

from __future__ import annotations

import sys
from pathlib import Path

import jiuwenbox

_CONFIGS_DIR = Path(jiuwenbox.__file__).resolve().parent / "configs"


def configs_dir() -> Path:
    """Directory containing default policy templates bundled with the wheel."""
    return _CONFIGS_DIR


def default_policy_path() -> Path:
    """Default ``default-policy.yaml`` path when ``JIUWENBOX_POLICY_PATH`` is unset."""
    return _CONFIGS_DIR / "default-policy.yaml"


def base_policy_path() -> Path:
    """ policy 路径 (框架 default, 随 wheel 升级只读).

    Windows: ``windows-policy.yaml``; 其它平台 (Linux): ``default-policy.yaml``.
    box-server 读 ``base_policy_path()`` (基底 default) + ``JIUWENBOX_POLICY_PATH``
    (用户副本 user_config) 合并, 不生成合并文件 (见 policy_merge).
    """
    name = "windows-policy.yaml" if sys.platform == "win32" else "default-policy.yaml"
    return _CONFIGS_DIR / name
