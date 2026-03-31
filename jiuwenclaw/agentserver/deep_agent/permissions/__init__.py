# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
JiuwenClaw 细粒度权限管控系统.

提供三级权限动作:
 - allow: 直接执行
 - ask:   弹出审批确认（支持本次允许/总是允许/拒绝）
 - deny:  拒绝执行

优先级规则:
 - deny 绝对否决: 任何匹配到的 deny 规则都不会被覆盖
 - 来源优先级 (用于 ask/allow 之间的决断):
   1. tools.<tool>.patterns[i]   工具级模式规则
   2. tools.<tool>.*             工具级默认
   3. defaults.*                 全局默认
 - 同一工具的 patterns 内部: deny > ask > allow (最严格者胜出)

使用示例::

    from jiuwenclaw.agentserver.permissions import (
        get_permission_engine,
        PermissionLevel,
    )

    engine = get_permission_engine()
    result = engine.check_permission(
        tool_name="mcp_exec_command",
        tool_args={"command": "ls -la"},
    )

    if result.is_allowed:
        ...
    elif result.needs_approval:
        # 审批流程由 react_agent._request_permission_approval 处理
        ...
"""

from jiuwenclaw.agentserver.deep_agent.permissions.core import (
    PermissionEngine,
    get_permission_engine,
    init_permission_engine,
    set_permission_engine,
)
from jiuwenclaw.agentserver.deep_agent.permissions.checker import (
    assess_command_risk_static,
    assess_command_risk_with_llm,
    check_tool_permissions,
)
from jiuwenclaw.agentserver.deep_agent.permissions.patterns import (
    build_command_allow_pattern,
    persist_external_directory_allow,
    persist_permission_allow_rule,
)
from jiuwenclaw.agentserver.deep_agent.permissions.models import (
    PermissionLevel,
    PermissionResult,
)

__all__ = [
    # Models
    "PermissionLevel",
    "PermissionResult",
    # Core
    "PermissionEngine",
    "init_permission_engine",
    "get_permission_engine",
    "set_permission_engine",
    # Guard
    "check_tool_permissions",
    # Persist
    "build_command_allow_pattern",
    "persist_permission_allow_rule",
    "persist_external_directory_allow",
    # Risk
    "assess_command_risk_static",
    "assess_command_risk_with_llm",
]
