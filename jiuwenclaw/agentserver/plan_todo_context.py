# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Plan 模式 todo 的当前 session 上下文。

由 JiuWenClawDeepAdapter._bind_runtime_cron_context 在每次请求前写入，
使无参构造的 ``TodoToolkit()`` 能够在当前会话上下文中定位 todo 状态。
在调用时能解析到当前 ``agent/sessions/{session_id}/`` 下的 ``todo.md``。

从 ``todo_toolkits`` 中拆出，保持工具模块不持有 ContextVar / set_token API。
"""

from __future__ import annotations

import contextvars
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 与 ``interface_deep`` 的 bind 中 ``session_id or "default"`` 一致
PLAN_TODO_SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jiuwenclaw_plan_todo_session_id",
    default="default",
)

# Sub-scope ContextVar：嵌套的 spawn/fork 调用栈拼成的字符串。
# 默认 ""（主 agent，等价旧行为）；多层 spawn/fork 用 "__" 拼接。
# 例: "subagent_697c240f"、"subagent_d0cb1234__fork_agent_a3b21f04"。

_SUB_SCOPE_SEP = "__"


def get_plan_todo_session_id() -> str:
    """当前协程/请求应使用的 session id（用于写 todo 文件）。"""
    v = PLAN_TODO_SESSION_ID.get() or "default"
    if v == "default":
        try:
            from openjiuwen.agent_teams.spawn.context import get_session_id as _team_sid

            t: Optional[str] = _team_sid()
            if t:
                return t
        except Exception as exc:
            logger.debug("resolve team session id skipped: %s", exc)
    return v


