# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ContextStore —— 在线执行 session 级 accumulator + 持久化。

职责（设计 §5.4 / §7.2）：
- session 级 accumulator dict（初始含 query/skill_name/env）+ 已完成节点集
  + scenario + 重试计数 + fallback 计数
- 复用 ``inputs.update(result)`` 语义（与现有 root PlanNode accumulator 一致）
- 持久化复用 ``node_artifact_store`` 的 session state 范式（pre_run/update_state/post_run）
- 按 ``session_id + task_id`` 索引，同一 session 多个独立任务互不串扰

session state key：``__skill_turbo_online_ctx__``，与现有
``__skill_turbo_node_artifacts__`` / ``__skill_turbo_resume_ctx__`` 同级。
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# SkillTurbo 在线上下文 session state key
# 与 node_artifact_store / permission_bridge 的 key 同级，共用一次 pre_run/post_run 落盘
SKILL_TURBO_ONLINE_CTX_KEY = "__skill_turbo_online_ctx__"

# HITL 中断现场 session state key（在线模式单节点重放用）
# 记录被中断的 plan_name + pending_tool_call_id，恢复时只重放该节点（非 root 重放）。
# 与 permission_bridge.SKILL_TURBO_RESUME_CTX_KEY 同级，但语义不同：
# - resume_ctx：批量模式整 plan_code + root 重放
# - online_interrupt：在线模式单节点重放 + ContextStore 衔接
SKILL_TURBO_ONLINE_INTERRUPT_KEY = "__skill_turbo_online_interrupt__"

# after_tool_call flush 后再清 ctx（避免 F2 读不到 task_progress）
# 按 session_id 隔离，避免多会话并发串扰
_pending_clear_by_sid: dict[str, bool] = {}


def mark_pending_clear_online_context(session: Any = None) -> None:
    """标记：StreamEventRail.after_tool_call flush 后应 clear ctx。"""
    sid = _resolve_session_id(session) if session is not None else "?"
    _pending_clear_by_sid[sid] = True


def consume_pending_clear_online_context(session: Any = None) -> bool:
    """读取并清除指定 session 的 pending clear 标记。"""
    sid = _resolve_session_id(session) if session is not None else "?"
    return bool(_pending_clear_by_sid.pop(sid, False))


@dataclass
class TurboContext:
    """在线执行 session 级上下文（accumulator 模式）。

    语义与现有 root PlanNode 的 ``inputs`` dict 一致——单 dict、节点读所需键、
    写 outputs 回去；只是生命周期从一次 ``root.run()`` 扩展到跨多次工具调用。
    """

    task_id: str                       # 任务实例 id（session+任务序号）
    skill_name: str                    # 源 skill 名（activate 锁定）
    scenario: str                      # 锁定的切面（activate 锁定）
    turbo_dir: str                     # turbo/ 目录绝对路径
    accumulator: dict[str, Any]        # 累积上下文；初始含 query/skill_name/skill_root/...
    completed: set[str]                # 已完成（含跳过）的 plan_name
    retry_count: dict[str, int]        # 每节点参数校验重试次数
    fallback_count: int               # 整任务累计 fallback 节点数
    fallback_nodes: list[str]          # 走过 fallback 的节点
    status: str                        # "running"|"completed"|"fallback_to_batch"|"fallback_to_skill_tool"
    started_at: float = field(default_factory=time.time)
    # plan_name → task.* 进度快照（online TaskList；显式 to_dict/from_dict）
    task_progress: dict[str, dict[str, Any]] = field(default_factory=dict)

    def update(self, node_outputs: dict[str, Any], plan_name: str) -> None:
        """节点执行后更新 accumulator + completed。

        复用现有 ``inputs.update(result)`` 语义：下游节点从同一 dict 取所需键。
        """
        if isinstance(node_outputs, dict):
            self.accumulator.update(node_outputs)
        self.completed.add(plan_name)

    def record_retry(self, plan_name: str) -> int:
        """参数校验失败时递增重试计数，返回当前重试次数。"""
        self.retry_count[plan_name] = self.retry_count.get(plan_name, 0) + 1
        return self.retry_count[plan_name]

    def record_fallback(self, plan_name: str) -> None:
        """单节点 fallback 时递增累计计数 + 记录节点。"""
        self.fallback_count += 1
        if plan_name not in self.fallback_nodes:
            self.fallback_nodes.append(plan_name)

    def to_dict(self) -> dict[str, Any]:
        """序列化为可持久化的 dict（set → list）。"""
        return {
            "task_id": self.task_id,
            "skill_name": self.skill_name,
            "scenario": self.scenario,
            "turbo_dir": self.turbo_dir,
            "accumulator": self.accumulator,
            "completed": list(self.completed),
            "retry_count": self.retry_count,
            "fallback_count": self.fallback_count,
            "fallback_nodes": self.fallback_nodes,
            "status": self.status,
            "started_at": self.started_at,
            "task_progress": self.task_progress,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TurboContext":
        """从持久化 dict 反序列化（list → set）。"""
        completed = data.get("completed", [])
        accumulator = data.get("accumulator", {})
        if not isinstance(accumulator, dict):
            logger.warning("[OnlineCtx] from_dict: accumulator type invalid, using {}")
            accumulator = {}
        retry_count = data.get("retry_count", {})
        if not isinstance(retry_count, dict):
            logger.warning("[OnlineCtx] from_dict: retry_count type invalid, using {}")
            retry_count = {}
        fallback_nodes = data.get("fallback_nodes", [])
        if not isinstance(fallback_nodes, list):
            logger.warning("[OnlineCtx] from_dict: fallback_nodes type invalid, using []")
            fallback_nodes = []
        task_progress = data.get("task_progress") or {}
        if not isinstance(task_progress, dict):
            logger.warning("[OnlineCtx] from_dict: task_progress type invalid, using {}")
            task_progress = {}
        return cls(
            task_id=str(data.get("task_id", "")),
            skill_name=str(data.get("skill_name", "")),
            scenario=str(data.get("scenario", "")),
            turbo_dir=str(data.get("turbo_dir", "")),
            accumulator=dict(accumulator),
            completed=set(completed) if isinstance(completed, (list, tuple, set)) else set(),
            retry_count=dict(retry_count),
            fallback_count=int(data.get("fallback_count", 0) or 0),
            fallback_nodes=list(fallback_nodes),
            status=str(data.get("status", "running")),
            started_at=float(data.get("started_at", time.time())),
            task_progress=dict(task_progress),
        )


def _resolve_session_id(session: Any) -> str:
    """统一获取 session ID，与 node_artifact_store._resolve_session_id 逻辑一致。"""
    if session is not None and callable(getattr(session, "get_session_id", None)):
        sid = session.get_session_id()
        if sid:
            return str(sid)
    for attr in ("session_id", "_session_id"):
        sid = getattr(session, attr, None)
        if sid:
            return str(sid)
    return "?"


def make_task_id(session_id: str) -> str:
    """生成任务实例 id：session_id + 毫秒时间戳 + 短随机。

    同一 session 多次 activate 产生不同 task_id，互不串扰。
    """
    ts = int(time.time() * 1000)
    short = uuid.uuid4().hex[:12]
    return f"{session_id}:{ts}:{short}"


async def save_online_context(
    session: Any,
    ctx: TurboContext,
    *,
    skip_post_run: bool = True,
    persist_reason: str = "active_parent_stream",
) -> bool:
    """持久化 TurboContext 到 session state（键 ``__skill_turbo_online_ctx__``）。

    复用 ``node_artifact_store.save_node_artifacts`` 的 pre_run/update_state/post_run 范式。
    调用方如果在已 pre_run 的上下文里，重复 pre_run 是 no-op。

    Args:
        session: openjiuwen Session 实例。
        ctx: 要持久化的 TurboContext。
        skip_post_run: 默认 True。活跃 Agent 流式请求内禁止对 parent post_run
            （会 close_stream，导致后续 task.* 被静默丢弃，见出站投递方案 F1a）。
            跨请求落盘依赖主 Agent 结束时的一次 post_run。
        persist_reason: 日志用原因标记。

    Returns:
        True 若 update_state 成功；False 表示持久化失败（已打 warning）。
    """
    if session is None:
        logger.warning("[OnlineCtx] save_online_context: session is None, skipping")
        return False
    sid = _resolve_session_id(session)
    payload = ctx.to_dict()
    try:
        await session.pre_run(inputs=None)
    except Exception as exc:
        logger.warning(
            "[OnlineCtx] save_online_context pre_run failed: sid=%s err=%s", sid, exc,
        )
        return False
    try:
        session.update_state({SKILL_TURBO_ONLINE_CTX_KEY: payload})
    except Exception as exc:
        logger.warning(
            "[OnlineCtx] save_online_context update_state failed: sid=%s err=%s", sid, exc,
        )
        return False
    if skip_post_run:
        logger.info(
            "[OnlineCtx] save skip_post_run=True sid=%s reason=%s task=%s",
            sid, persist_reason, ctx.task_id,
        )
        return True
    try:
        await session.post_run()
        logger.info(
            "[OnlineCtx] save skip_post_run=False sid=%s reason=%s task=%s",
            sid, persist_reason or "final_persist", ctx.task_id,
        )
        return True
    except Exception as exc:
        logger.warning(
            "[OnlineCtx] save_online_context post_run failed: sid=%s err=%s", sid, exc,
        )
        return False


async def load_online_context(session: Any) -> TurboContext | None:
    """从 session state 读取 TurboContext。无记录返回 None。

    与 ``node_artifact_store.load_node_artifacts`` 范式一致：不自行 pre_run，
    由调用方在已 pre_run 的上下文里调用。
    """
    if session is None:
        logger.warning("[OnlineCtx] load_online_context: session is None")
        return None
    sid = _resolve_session_id(session)
    try:
        state = session.get_state(SKILL_TURBO_ONLINE_CTX_KEY)
    except Exception as exc:
        logger.warning(
            "[OnlineCtx] load_online_context get_state failed: sid=%s err=%s", sid, exc,
        )
        return None
    if not isinstance(state, dict) or not state.get("task_id"):
        logger.debug("[OnlineCtx] load_online_context: no records sid=%s", sid)
        return None
    try:
        ctx = TurboContext.from_dict(state)
        logger.info(
            "[OnlineCtx] load_online_context: found sid=%s task=%s skill=%s scenario=%s completed=%d",
            sid, ctx.task_id, ctx.skill_name, ctx.scenario, len(ctx.completed),
        )
        return ctx
    except Exception as exc:
        logger.warning(
            "[OnlineCtx] load_online_context from_dict failed: sid=%s err=%s", sid, exc,
        )
        return None


async def clear_online_context(
    session: Any,
    *,
    persist: bool = True,
    skip_post_run: bool = True,
) -> None:
    """清除在线上下文（任务完成/回退后调用）。

    Args:
        session: openjiuwen Session 实例。
        persist: True（默认）时 pre_run → update_state(None)，保证内存/state 清理。
            False 时仅内存清理。
        skip_post_run: 默认 True。活跃父会话流式请求内禁止 post_run（F1a），
            跨请求由主 Agent 结束时的 post_run 刷 checkpointer。
    """
    if session is None:
        return
    sid = _resolve_session_id(session)
    try:
        if persist:
            try:
                await session.pre_run(inputs=None)
            except Exception as exc:
                # 若调用方已 post_run，pre_run 可能失败；仍尝试 update_state 清内存态
                logger.warning(
                    "[OnlineCtx] clear_online_context pre_run failed "
                    "(still attempting update_state): sid=%s err=%s",
                    sid, exc,
                )
        session.update_state({SKILL_TURBO_ONLINE_CTX_KEY: None})
        logger.info(
            "[OnlineCtx] clear_online_context: cleared sid=%s persist=%s skip_post_run=%s",
            sid, persist, skip_post_run,
        )
        if persist and not skip_post_run:
            try:
                await session.post_run()
            except Exception as exc:
                logger.warning(
                    "[OnlineCtx] clear_online_context post_run failed: sid=%s err=%s",
                    sid, exc,
                )
    except Exception as exc:
        logger.debug(
            "[OnlineCtx] clear_online_context update_state failed: %s", exc,
        )


# ── HITL 中断现场持久化（在线模式单节点重放，设计 §6.5）──


async def save_online_interrupt_state(
    session: Any,
    *,
    interrupted_plan_name: str,
    pending_tool_call_id: str,
    skip_post_run: bool = True,
) -> None:
    """持久化在线 HITL 中断现场到 session state。

    在线模式无 root，中断点在某个 group/叶节点内部。恢复时只需重放该节点，
    故只记录 interrupted_plan_name + pending_tool_call_id（ContextStore 已由
    save_online_context 单独持久化，含 accumulator/completed/scenario/skill_name）。

    Args:
        session: openjiuwen Session 实例。
        interrupted_plan_name: 被中断的节点名（group 入口或叶节点）。
        pending_tool_call_id: HITL 工具调用 id（重放时注入 user_input 的锚点）。
        skip_post_run: 默认 True（F1a：禁止活跃父会话中途 post_run）。
    """
    if session is None:
        logger.warning("[OnlineCtx] save_online_interrupt_state: session is None, skipping")
        return
    sid = _resolve_session_id(session)
    entry = {
        "interrupted_plan_name": interrupted_plan_name,
        "pending_tool_call_id": pending_tool_call_id,
    }
    try:
        await session.pre_run(inputs=None)
    except Exception as exc:
        logger.warning(
            "[OnlineCtx] save_online_interrupt_state pre_run failed: sid=%s err=%s", sid, exc,
        )
        return
    try:
        session.update_state({SKILL_TURBO_ONLINE_INTERRUPT_KEY: entry})
    except Exception as exc:
        logger.warning(
            "[OnlineCtx] save_online_interrupt_state update_state failed: sid=%s err=%s", sid, exc,
        )
        return
    if skip_post_run:
        logger.info(
            "[OnlineCtx] interrupt save skip_post_run=True sid=%s plan=%s",
            sid, interrupted_plan_name,
        )
        return
    try:
        await session.post_run()
        logger.info(
            "[OnlineCtx] save_online_interrupt_state: persisted OK sid=%s plan=%s tcid=%s",
            sid, interrupted_plan_name, pending_tool_call_id,
        )
    except Exception as exc:
        logger.warning(
            "[OnlineCtx] save_online_interrupt_state post_run failed: sid=%s err=%s", sid, exc,
        )


async def load_online_interrupt_state(session: Any) -> dict[str, Any] | None:
    """从 session state 读取在线 HITL 中断现场。

    返回 ``{"interrupted_plan_name": ..., "pending_tool_call_id": ...}`` 或 None。
    不自行 pre_run，由调用方在已 pre_run 的上下文里调用（与 load_online_context 一致）。
    """
    if session is None:
        return None
    sid = _resolve_session_id(session)
    try:
        state = session.get_state(SKILL_TURBO_ONLINE_INTERRUPT_KEY)
    except Exception as exc:
        logger.warning(
            "[OnlineCtx] load_online_interrupt_state get_state failed: sid=%s err=%s", sid, exc,
        )
        return None
    if not isinstance(state, dict) or not state.get("interrupted_plan_name"):
        return None
    logger.info(
        "[OnlineCtx] load_online_interrupt_state: found sid=%s plan=%s tcid=%s",
        sid, state.get("interrupted_plan_name"), state.get("pending_tool_call_id"),
    )
    return dict(state)


async def clear_online_interrupt_state(session: Any) -> None:
    """清除在线 HITL 中断现场（重放完成后调用）。

    调用方应已 pre_run，并负责 post_run 持久化。
    """
    if session is None:
        return
    try:
        session.update_state({SKILL_TURBO_ONLINE_INTERRUPT_KEY: None})
        logger.debug(
            "[OnlineCtx] clear_online_interrupt_state: cleared sid=%s",
            _resolve_session_id(session),
        )
    except Exception as exc:
        logger.debug(
            "[OnlineCtx] clear_online_interrupt_state update_state failed: %s", exc,
        )


__all__ = [
    "SKILL_TURBO_ONLINE_CTX_KEY",
    "SKILL_TURBO_ONLINE_INTERRUPT_KEY",
    "TurboContext",
    "make_task_id",
    "save_online_context",
    "load_online_context",
    "clear_online_context",
    "mark_pending_clear_online_context",
    "consume_pending_clear_online_context",
    "save_online_interrupt_state",
    "load_online_interrupt_state",
    "clear_online_interrupt_state",
]
