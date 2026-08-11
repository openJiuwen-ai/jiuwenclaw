# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillTurbo 在线执行工具化 —— 将在线执行入口封装为 DeepAgent 的 @tool。

- skill_turbo_tool_entry：在线执行薄工具（discover / activate / execute 单 PlanNode）
- ContextVar：向工具函数注入 adapter、请求 metadata、resume 输入
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any

from openjiuwen.core.foundation.tool import tool

if TYPE_CHECKING:
    from openjiuwen.core.session.agent import Session

logger = logging.getLogger(__name__)

# ── ContextVar：在 before_tool_call 中注入，供工具函数读取 ──
_current_skill_turbo_adapter: ContextVar[Any] = ContextVar(
    "current_skill_turbo_adapter", default=None
)

# ── ContextVar：SkillTurbo HITL resume 输入 ──
# adapter 检测 resume 时通过此 ContextVar 传递 user_input，
# skill_turbo_tool 通过 _get_resume_user_input 读取后传给 executor.set_pending_resume。
# （skill_turbo_tool 工具签名无 ctx 参数，无法通过 ctx.extra 传递，故用 ContextVar）
_skill_turbo_resume_input: ContextVar[Any] = ContextVar(
    "skill_turbo_resume_input", default=None
)


def set_skill_turbo_resume_input(user_input: Any) -> Token:
    """设置 resume 的 user_input（adapter 重执前调用）."""
    return _skill_turbo_resume_input.set(user_input)


def get_skill_turbo_resume_input() -> Any:
    """获取 resume 的 user_input（skill_turbo_tool 内读取）."""
    return _skill_turbo_resume_input.get()


def reset_skill_turbo_resume_input(token: Token) -> None:
    """恢复之前的 resume 绑定."""
    _skill_turbo_resume_input.reset(token)


def set_current_skill_turbo_adapter(adapter: Any) -> Token:
    """绑定当前 async 上下文的 DeepAdapter 实例，返回 Token 用于 reset。"""
    return _current_skill_turbo_adapter.set(adapter)


def get_current_skill_turbo_adapter() -> Any:
    """获取当前上下文的 DeepAdapter 实例。"""
    return _current_skill_turbo_adapter.get()


def reset_current_skill_turbo_adapter(token: Token) -> None:
    """恢复之前的 adapter 绑定。"""
    _current_skill_turbo_adapter.reset(token)


# ── ContextVar：当前请求的 metadata ──
# 在 _update_runtime_config 中设置（md 是局部变量，无竞态），
# skill_turbo 通过 get_current_request_metadata() 读取，
# 替代 adapter._current_request_metadata 实例属性（并发覆盖风险）。
_current_request_metadata: ContextVar[Any] = ContextVar(
    "current_request_metadata", default=None
)


def set_current_request_metadata(metadata: Any) -> Token:
    """绑定当前 async 上下文的请求 metadata，返回 Token 用于 reset。"""
    return _current_request_metadata.set(metadata)


def get_current_request_metadata() -> Any:
    """获取当前上下文的请求 metadata。"""
    return _current_request_metadata.get()


def reset_current_request_metadata(token: Token) -> None:
    """恢复之前的 metadata 绑定。"""
    _current_request_metadata.reset(token)


# ─────────────────────────────────────────────────────────────────────────────
# skill_turbo_tool — 在线执行薄工具（activate + execute 单 PlanNode）
# ─────────────────────────────────────────────────────────────────────────────


@tool(
    name="skill_turbo_tool",
    description=(
        "Skill Turbo 在线执行工具。用于加速执行已有 turbo 产物的 skill（如 pptx-craft）。\n"
        "三种模式：\n"
        "1. discover（scenario 省略 + plan_name 省略）：返回场景清单 + 触发条件 + 选择规则，*供选择 scenario*\n"
        "2. activate（scenario 指定 + plan_name 省略）：返回 schema 概览（plan_tasks），*供规划 todo*\n"
        "3. execute（plan_name 非空）：执行单个 PlanNode，返回产物摘要（路径+标量）\n"
        "参数：skill_name（源 skill 名）、scenario（任务切面，None=discover）、plan_name（节点名）、"
        "inputs（节点输入 dict）"
    ),
)
async def skill_turbo_tool_entry(
    skill_name: str,
    scenario: str | None = None,
    plan_name: str | None = None,
    inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在线执行 skill turbo 的单个 PlanNode.

    Args:
        skill_name: 源 skill 名，如 "pptx-craft"
        scenario: 任务切面，如 "create_ppt"
        plan_name: None=activate（返回 schema 概览）；非空=execute（跑单节点）
        inputs: execute 时该节点所需输入（Agent 从历史工具结果组装）
    """
    from jiuwenswarm.agents.skill_turbo.online.skill_turbo_tool import (
        skill_turbo_tool,
    )

    return await skill_turbo_tool(skill_name, scenario, plan_name, inputs)


def get_skill_turbo_online_tools() -> list:
    """返回在线执行工具列表，供 interface_deep.py 注册。"""
    return [skill_turbo_tool_entry]
