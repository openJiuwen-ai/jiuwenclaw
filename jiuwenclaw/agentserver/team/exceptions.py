# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Team 异常定义.

定义 Team 模块中使用的各种异常类型.
"""

from __future__ import annotations


class TeamError(Exception):
    """Team 基础异常类."""
    pass


class TeamCreateError(TeamError):
    """Team 创建失败."""
    pass


class TeamRecoverError(TeamError):
    """Team 恢复失败."""
    pass


class TeamInteractError(TeamError):
    """Team 交互失败."""
    pass


class TeamConfigError(TeamError):
    """Team 配置错误."""
    pass


class TeamMonitorError(TeamError):
    """Team Monitor 错误."""
    pass


class TeamSessionError(TeamError):
    """Team 会话错误."""
    pass


class TeamStorageError(TeamError):
    """Team 存储错误."""
    pass


class TeamDissolveError(TeamError):
    """Team 保上下文解散（dissolve keep-context）失败."""
    pass


class TeamDissolveConflictError(TeamDissolveError):
    """解散被拒绝：runtime 正在运行（stream task 存活或池条目 RUNNING）."""
    pass


class TeamDissolveUnsupportedError(TeamDissolveError):
    """解散被拒绝：当前模式不支持（如分布式模式）."""
    pass


class TeamDissolveNameMismatchError(TeamDissolveError):
    """解散被拒绝：传入 team_name 与 session 实际团队名不符."""
    pass
