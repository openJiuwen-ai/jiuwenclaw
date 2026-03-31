# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""权限系统数据模型."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PermissionLevel(str, Enum):
    """权限级别.

    - ALLOW: 直接执行，无需确认
    - ASK:   弹出确认框，用户决定
    - DENY:  拒绝执行，返回错误
    """
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class PermissionResult:
    permission: PermissionLevel
    matched_rule: str | None = None
    reason: str | None = None
    external_paths: list[str] | None = None
    risk: dict | None = None

    @property
    def is_allowed(self) -> bool:
        return self.permission == PermissionLevel.ALLOW

    @property
    def is_denied(self) -> bool:
        return self.permission == PermissionLevel.DENY

    @property
    def needs_approval(self) -> bool:
        return self.permission == PermissionLevel.ASK


@dataclass
class PatternRule:
    """模式匹配规则."""
    pattern: str
    permission: PermissionLevel
    description: str = ""
    rule_id: str = ""

