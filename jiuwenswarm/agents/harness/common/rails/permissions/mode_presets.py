# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""JiuwenSwarm 产品权限档位 preset（不落盘字段形状）。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

PermissionMode = Literal["full_access", "auto", "strict"]

MODE_PRESETS: dict[PermissionMode, dict[str, Any]] = {
    "full_access": {
        "severity_map": "normal",
        "sandbox_intent": "optional",
        "defaults": {"*": "allow"},
        "file_guard": {"enabled": False},
        "network": {
            "defaults": "allow",
            "ignore_user_host_rules": True,
        },
        "findings_escalate": False,
    },
    "auto": {
        "severity_map": "normal",
        "sandbox_intent": "required",
        "defaults": {"*": "allow"},
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "allow", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "allow", "exec": "allow"},
        },
        "network": {
            "defaults": "allow",
            "ignore_user_host_rules": False,
        },
        "findings_escalate": True,
    },
    "strict": {
        "severity_map": "strict",
        "sandbox_intent": "required",
        "defaults": {"*": "ask"},
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "ask", "exec": "ask"},
        },
        "network": {
            "defaults": "ask",
            "ignore_user_host_rules": False,
        },
        "findings_escalate": True,
    },
}


def get_mode_preset(mode: PermissionMode) -> dict[str, Any]:
    """返回指定 mode 的深拷贝 preset。"""
    return deepcopy(MODE_PRESETS[mode])


__all__ = ["MODE_PRESETS", "get_mode_preset"]
