# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Plan 模式 todo / skill_step 的当前 session 上下文。

由 JiuWenClawDeepAdapter._bind_runtime_cron_context 在每次请求前写入，
使无参构造的 ``TodoToolkit()`` / ``SkillStepToolkit()``（由 SkillProtocolPromptRail 等注册）
在调用时能解析到当前 ``agent/sessions/{session_id}/`` 下的 ``todo.md`` / ``skill_step.md``。

子 agent（spawn / fork）通过 :func:`push_subscope` / :func:`pop_subscope`
在子协程入口处叠加一层 ``sub_scope`` 命名空间，使每个 spawn / fork 实例
拥有独立的 ``skill_step__{sub_scope}.md`` 文件，避免共享同一份 plan
导致的 create-already-exists 冲突与 fork 间的 idx / 状态覆盖。

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
PLAN_TODO_SUB_SCOPE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "jiuwenclaw_plan_todo_sub_scope",
    default="",
)

_SUB_SCOPE_SEP = "__"


def get_plan_todo_session_id() -> str:
    """当前协程/请求应使用的 session id（用于写 todo / skill_step 文件）。"""
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


def get_plan_todo_sub_scope() -> str:
    """当前协程的 sub_scope 字符串。主 agent 返回 ""。"""
    return PLAN_TODO_SUB_SCOPE.get() or ""


def push_subscope(label: str) -> contextvars.Token:
    """在子协程入口叠加一层 sub_scope。返回 Token 给 finally pop。

    label 必须在父 scope 内唯一（spawn / fork 的 task_id 已经天然唯一）。
    sub_scope 值是父 scope 与 label 用 ``__`` 拼接的结果，多层嵌套自动叠加。
    """
    label = (label or "").strip()
    if not label:
        # 空 label 等价于不 push，但仍返回 Token 让 caller 安全 pop。
        return PLAN_TODO_SUB_SCOPE.set(PLAN_TODO_SUB_SCOPE.get())
    parent = PLAN_TODO_SUB_SCOPE.get() or ""
    new_scope = f"{parent}{_SUB_SCOPE_SEP}{label}" if parent else label
    return PLAN_TODO_SUB_SCOPE.set(new_scope)


def pop_subscope(token: contextvars.Token) -> None:
    """配合 :func:`push_subscope` 使用。务必在 finally 中调用。"""
    try:
        PLAN_TODO_SUB_SCOPE.reset(token)
    except Exception as exc:
        logger.debug("pop_subscope failed (token may be stale): %s", exc)
