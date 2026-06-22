# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""RePlan ↔ PermissionInterruptRail 胶水层（纯函数，无业务状态）。

职责：
1. 构造 ``AgentCallbackContext``（含 resume 用户输入），交给护栏链 ``before_tool_call``。
2. 从 ``AbortError`` 中抽出 ``ToolInterruptException``（含 ``InterruptRequest`` 与 ``ToolCall``）。
3. 把 RePlan 的「断点上下文」存到当前 openjiuwen ``Session`` 的状态里，键 ``__replan_resume_ctx__``，
   不引入第二份内存存储。

设计原则参见 RePlanAgent 安全护栏新方案 v2 §4。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)

# RePlan 自有 session state key —— 与 openjiuwen 自身命名空间区分，所以前后用双下划线。
REPLAN_RESUME_CTX_KEY = "__replan_resume_ctx__"

logger = logging.getLogger(__name__)


@dataclass
class ReplanToolCall:
    """RePlan 内部用的轻量 tool_call 对象。

    不依赖 openjiuwen pydantic ``ToolCall``（它强制要求 ``arguments: str``、
    需要 ``type`` 字段），rail 只通过 ``id/name/arguments`` 属性访问，
    用 dataclass 满足鸭子类型即可。
    """

    id: str
    name: str
    arguments: dict[str, Any]


def build_tool_ctx(
    *,
    session: Any,
    tool_name: str,
    tool_args: dict[str, Any],
    tool_call_id: str,
    resume_user_input: Any | None = None,
) -> AgentCallbackContext:
    """构造一个用于 ``before_tool_call`` / ``after_tool_call`` 的 ctx。

    Args:
        session: 当前 openjiuwen Session 实例（可为 None，rail 内部会兼容）。
        tool_name: 工具名称。
        tool_args: 工具参数（kwargs 透传）。
        tool_call_id: 本次 tool 调用 id；resume 时必须与中断时一致，
            才能被 ``BaseInterruptRail._get_user_input`` 命中。
        resume_user_input: 用户对该 tool_call_id 的回复。``None`` 表示首次调用。
            可以是 ``ConfirmPayload`` 实例 / dict / 任意 rail 接受的载荷。
    """
    tool_call = ReplanToolCall(id=tool_call_id, name=tool_name, arguments=tool_args)
    extra: dict[str, Any] = {}
    if resume_user_input is not None:
        # rail 通过 ctx.extra[RESUME_USER_INPUT_KEY] 取用户回复
        extra[RESUME_USER_INPUT_KEY] = resume_user_input
    return AgentCallbackContext(
        agent=None,
        session=session,
        inputs=ToolCallInputs(
            tool_name=tool_name,
            tool_call=tool_call,
            tool_args=tool_args,
        ),
        context=None,
        extra=extra,
    )


def extract_tool_interrupt(exc: BaseException) -> ToolInterruptException | None:
    """沿 ``__cause__`` / ``cause`` 链抽出 ``ToolInterruptException``。

    PermissionInterruptRail 抛出的链路是：
        ``AbortError(reason=..., cause=ToolInterruptException(request, tool_call))``

    其中 ``cause`` 既被设置到 ``AbortError.cause`` 字段，也通过 ``raise ... from cause``
    挂到 ``__cause__``。两者择一即可。
    """
    visited: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in visited:
        if isinstance(cur, ToolInterruptException):
            return cur
        visited.add(id(cur))
        nxt = getattr(cur, "__cause__", None)
        if nxt is None:
            nxt = getattr(cur, "cause", None)
        cur = nxt
    return None


def is_blocking_abort(exc: BaseException) -> bool:
    """判断异常是否承载工具中断（HITL）语义。"""
    if not isinstance(exc, AbortError):
        return False
    return extract_tool_interrupt(exc) is not None


# ──────────────────────── Resume Context（仅放 session state） ────────────────────────


async def save_resume_ctx(
    session: Any,
    *,
    plan_code: str,
    inputs: dict[str, Any],
    pending_tool_call_id: str,
) -> None:
    """中断时保存断点上下文，供下一次请求重放。

    会自己 pre_run+post_run 保证持久化（跨请求靠 checkpointer 取回）。
    调用方如果在已 pre_run 的上下文里，重复 pre_run 是 no-op。
    """
    if session is None:
        logger.warning("[RePlanResume] save_resume_ctx: session is None, skipping")
        return
    sid = getattr(session, "session_id", "?")
    logger.info(
        "[RePlanResume] save_resume_ctx: sid=%s tcid=%s plan_code_len=%d",
        sid, pending_tool_call_id, len(plan_code or ""),
    )
    try:
        await session.pre_run(inputs=None)
    except Exception as e:
        logger.warning("[RePlanResume] save_resume_ctx pre_run failed: sid=%s err=%s", sid, e)
    session.update_state({
        REPLAN_RESUME_CTX_KEY: {
            "plan_code": plan_code,
            "inputs": dict(inputs),
            "pending_tool_call_id": pending_tool_call_id,
        }
    })
    try:
        await session.post_run()
        logger.info("[RePlanResume] save_resume_ctx: persisted OK sid=%s", sid)
    except Exception as e:
        logger.warning("[RePlanResume] save_resume_ctx post_run failed: sid=%s err=%s", sid, e)


async def load_resume_ctx(session: Any) -> dict[str, Any] | None:
    """读取断点上下文。返回 None 表示无可恢复的 RePlan 中断。

    会自己 pre_run 从 checkpointer 加载 state。
    """
    if session is None:
        logger.warning("[RePlanResume] load_resume_ctx: session is None")
        return None
    sid = getattr(session, "session_id", "?")
    try:
        await session.pre_run(inputs=None)
    except Exception as e:
        logger.warning("[RePlanResume] load_resume_ctx pre_run failed: sid=%s err=%s", sid, e)
        return None
    try:
        state = session.get_state(REPLAN_RESUME_CTX_KEY)
    except Exception as e:
        logger.warning("[RePlanResume] load_resume_ctx get_state failed: sid=%s err=%s", sid, e)
        return None
    if isinstance(state, dict) and state.get("plan_code"):
        logger.info(
            "[RePlanResume] load_resume_ctx: found ctx sid=%s tcid=%s",
            sid, state.get("pending_tool_call_id"),
        )
        return state
    logger.info("[RePlanResume] load_resume_ctx: no ctx found sid=%s state_type=%s", sid, type(state).__name__)
    return None


async def clear_resume_ctx(session: Any) -> None:
    """清除断点上下文（resume 跑通后调用）。

    调用方应已 pre_run，并负责 post_run 持久化。
    此函数只 update in-memory state，不触发 post_run，
    避免与调用方自己的 post_run 重复（重复 post_run 可能导致事件重复触发）。
    """
    if session is None:
        return
    try:
        session.update_state({REPLAN_RESUME_CTX_KEY: None})
    except Exception as exc:
        logger.debug("[RePlanResume] clear_resume_ctx update_state failed: %s", exc)
