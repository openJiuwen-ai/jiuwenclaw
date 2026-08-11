# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillTurbo ↔ PermissionInterruptRail 胶水层（纯函数，无业务状态）。

职责：
1. 构造 ``AgentCallbackContext``（含 resume 用户输入），交给护栏链 ``before_tool_call``。
2. 从 ``AbortError`` 中抽出 ``ToolInterruptException``（含 ``InterruptRequest`` 与 ``ToolCall``）。
3. 将 SkillTurbo 的「断点上下文」存入 openjiuwen ``Session`` state（键 ``__skill_turbo_resume_ctx__``），
   经 checkpointer 持久化，保证跨 worker/多实例部署的 HITL 恢复可靠。
4. 提供 ``set_skill_turbo_id`` 使 SkillTurbo checkpointer key 与 DeepAgent 隔离。

设计原则参见 SkillTurbo 安全护栏新方案 v2 §4。
"""


from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)

# AbortError 经 plan_node 统一 re-export，不在本模块直连 openjiuwen（见 plan_node 注释）。
from jiuwenswarm.agents.skill_turbo.plan_node import AbortError

# SkillTurbo 自有 session state key —— 与 openjiuwen 自身命名空间区分，前后用双下划线。
SKILL_TURBO_RESUME_CTX_KEY = "__skill_turbo_resume_ctx__"

# SkillTurbo 专用 agent_id 后缀：executor 和 resume 读取时用 '{card.id}__skill_turbo'，
# 使 checkpointer key 与 DeepAgent 隔离，避免 DeepAgent 的 post_run 覆盖
# executor 写入的 resume_ctx / node_artifacts。
SKILL_TURBO_ID_SUFFIX = "__skill_turbo"

logger = logging.getLogger(__name__)


def set_skill_turbo_id(session: Any, card: Any) -> None:
    """将 session 的 agent_id 设为 '{card.id}__skill_turbo'，使 SkillTurbo 的 checkpointer
    key 与 DeepAgent 隔离，避免 post_run 互相覆盖。

    必须在 session.pre_run() 之前调用。
    对 FakeSession 等无 _inner 的 stub 是 no-op。
    """
    if session is None or card is None:
        return
    card_id = getattr(card, "id", None)
    if not card_id:
        return
    inner = getattr(session, "_inner", None)
    if inner is None:
        return
    try:
        config = inner.config()
        skill_turbo_id = f"{card_id}{SKILL_TURBO_ID_SUFFIX}"
        config.set_agent_config(type("SkillTurboAgentConfig", (), {"id": skill_turbo_id})())
        logger.debug("[SkillTurboResume] set_skill_turbo_id: %s", skill_turbo_id)
    except Exception as exc:
        logger.warning("[SkillTurboResume] set_skill_turbo_id failed: %s", exc)


@dataclass
class SkillTurboToolCall:
    """SkillTurbo 内部用的轻量 tool_call 对象。

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
    tool_call = SkillTurboToolCall(id=tool_call_id, name=tool_name, arguments=tool_args)
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


def build_interaction_output_from_abort(
    exc: BaseException,
) -> Any | None:
    """从 AbortError 构造 ``OutputSchema(type="__interaction__")``。

    从 ``AbortError`` 的 cause 链抽出 ``ToolInterruptException``，再用
    ``ToolCallInterruptRequest.from_tool_call`` 包装，最终构造
    ``InteractionOutput(id=tcid, value=tool_call_request)`` 并放入
    ``OutputSchema(type="__interaction__", payload=interaction_payload)``。

    供 SkillTurbo executor 中断侧主动写流使用。

    Args:
        exc: ``AbortError``（其 cause 链含 ``ToolInterruptException``）。

    Returns:
        ``OutputSchema`` 实例，或 ``None``（无 tic / 构造失败）。
    """
    from openjiuwen.core.session.interaction.interaction import InteractionOutput
    from openjiuwen.core.session.stream import OutputSchema
    from openjiuwen.core.single_agent.interrupt.response import (
        ToolCallInterruptRequest,
    )

    tic = extract_tool_interrupt(exc)
    if tic is None:
        logger.warning(
            "[SkillTurboBridge] build_interaction_output_from_abort: no ToolInterruptException in cause chain"
        )
        return None

    tc_id = tic.tool_call.id if tic.tool_call else ""

    try:
        tool_call_request = ToolCallInterruptRequest.from_tool_call(
            tic.request, tic.tool_call,
        )
    except Exception as tcr_exc:
        tool_call_request = tic.request
        logger.warning(
            "[SkillTurboBridge] from_tool_call failed: %s; falling back to raw tic.request",
            tcr_exc,
            exc_info=True,
        )

    interaction_payload = InteractionOutput(
        id=tc_id or "",
        value=tool_call_request,
    )
    return OutputSchema(
        type="__interaction__",
        index=0,
        payload=interaction_payload,
    )


def _get_sid(session: Any) -> str:
    if session is not None and callable(getattr(session, "get_session_id", None)):
        sid = session.get_session_id()
        return str(sid) if sid else ""
    return ""


# resume_ctx 经 session state + checkpointer 持久化，保证跨 worker/多实例
# 部署的 HITL 恢复可靠（同一 session_id 在任何进程都能从 checkpointer 读到）。


async def save_resume_ctx(
    session: Any,
    *,
    skill_name: str,
    scenario: str,
    plan_name: str,
    inputs: dict[str, Any],
    pending_tool_call_id: str,
) -> None:
    """保存 Skill Turbo 执行的断点上下文。

    存储 skill_name/scenario/plan_name/inputs + pending_tool_call_id，
    用于 HITL resume 时重执单节点。
    """
    if session is None:
        logger.warning("[SkillTurboResume] save: session is None")
        return
    sid = _get_sid(session)
    entry = {
        "skill_name": skill_name,
        "scenario": scenario,
        "plan_name": plan_name,
        "inputs": dict(inputs),
        "pending_tool_call_id": pending_tool_call_id,
    }
    try:
        session.update_state({SKILL_TURBO_RESUME_CTX_KEY: entry})
        await session.post_run()
        logger.info(
            "[SkillTurboResume] saved: sid=%s plan=%s tcid=%s",
            sid,
            plan_name,
            pending_tool_call_id,
        )
    except Exception as e:
        logger.warning("[SkillTurboResume] save failed: sid=%s err=%s", sid, e)


async def load_resume_ctx(session: Any) -> dict[str, Any] | None:
    """加载 Skill Turbo 执行的断点上下文。"""
    if session is None:
        return None
    sid = _get_sid(session)
    try:
        await session.pre_run(inputs=None)
        state = session.get_state(SKILL_TURBO_RESUME_CTX_KEY)
        if isinstance(state, dict) and state.get("skill_name"):
            logger.info(
                "[SkillTurboResume] loaded: sid=%s plan=%s tcid=%s",
                sid,
                state.get("plan_name"),
                state.get("pending_tool_call_id"),
            )
            return copy.deepcopy(state)
    except Exception as e:
        logger.warning("[SkillTurboResume] load failed: sid=%s err=%s", sid, e)
    return None


async def clear_resume_ctx(session: Any) -> None:
    """清除 Skill Turbo 执行的断点上下文。"""
    if session is None:
        return
    try:
        session.update_state({SKILL_TURBO_RESUME_CTX_KEY: None})
        logger.info("[SkillTurboResume] cleared: sid=%s", _get_sid(session))
    except Exception:
        logger.debug("[SkillTurboResume] clear failed", exc_info=True)
