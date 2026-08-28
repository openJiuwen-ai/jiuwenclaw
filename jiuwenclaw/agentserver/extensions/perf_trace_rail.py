# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""PerfTraceRail - 性能定位日志 Rail。

通过 DeepAgentRail 钩子在请求各阶段（invoke / model / tool / iter）打印 INFO 级日志，
带统一 trace_id（request_id）和每阶段开始/结束/耗时，用于定位性能问题。

不依赖 OTel 后端，不改 core，通过 AGENT_EXTRA_RAILS 挂载。

用法::

    export AGENT_EXTRA_RAILS=jiuwenclaw.agentserver.extensions.perf_trace_rail
    # 可选关闭（默认开）
    # export PERF_TRACE_ENABLED=false
    # 详情日志（请求/响应原文，默认关；敏感模式下强制不打印）
    # export PERF_TRACE_DETAIL=true

trace_id 来源：优先读 DeepAdapter 在 invoke 前设置的请求级 _request_context.request_id，
回退 session_id，最后兜底生成。回调框架按 priority 降序执行（高者先跑）。

skill 阶段：skill 执行走 skill_step / skill_complete tool，before/after_tool_call 自动
捕获（tool_name=skill_step），无需专门 skill 钩子。

iter 语义：iter=外层 task-loop 迭代号（before_task_iteration 递增），不是内层 ReAct
model_call 次数；一轮 iter 内可能含多次 model_call，它们共用同一 iter 号。

日志样例（一次请求，2 轮 ReAct）::

    [perf] trace_id=req_xxx session_id=sess_yyy phase=invoke start
    [perf] trace_id=req_xxx session_id=sess_yyy phase=iter start iter=1
    [perf] trace_id=req_xxx session_id=sess_yyy phase=model start iter=1
    [perf] trace_id=req_xxx session_id=sess_yyy phase=model end iter=1 elapsed_ms=5547.8
    [perf] trace_id=req_xxx session_id=sess_yyy phase=tool start tool=search_web
    [perf] trace_id=req_xxx session_id=sess_yyy phase=tool end tool=search_web elapsed_ms=1230.5
    [perf] trace_id=req_xxx session_id=sess_yyy phase=iter end iter=1 elapsed_ms=6790.3
    [perf] trace_id=req_xxx session_id=sess_yyy phase=iter start iter=2
    [perf] trace_id=req_xxx session_id=sess_yyy phase=model start iter=2
    [perf] trace_id=req_xxx session_id=sess_yyy phase=model end iter=2 elapsed_ms=3201.1
    [perf] trace_id=req_xxx session_id=sess_yyy phase=iter end iter=2 elapsed_ms=3205.4
    [perf] trace_id=req_xxx session_id=sess_yyy phase=invoke end elapsed_ms=10002.7
"""

from __future__ import annotations

import functools
import os
import time
from contextvars import ContextVar
from typing import Any

from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.utils import logger

# 请求级 trace_id（before_invoke 设置，整个请求生命周期内所有阶段日志共用）。
# ContextVar 在 asyncio task 级别自动隔离，并发请求互不干扰。
_trace_id: ContextVar[str] = ContextVar("perf_trace_id", default="")
_session_id: ContextVar[str] = ContextVar("perf_session_id", default="")
# 各阶段开始时间：key = "phase:call_id"，value = perf_counter。
# 用 call_id 区分同一阶段并发的多次调用（如一轮多个 tool）。
_starts: ContextVar[dict | None] = ContextVar("perf_starts", default=None)
# 请求级迭代计数（before_task_iteration 递增）
_iter: ContextVar[int] = ContextVar("perf_iter", default=0)

_ENABLED = os.getenv("PERF_TRACE_ENABLED", "true").strip().lower() not in (
    "false", "0", "no", "off",
)
# 是否打印请求/响应详情（user query / response / tool args / tool result）
# 默认关：详情会打印用户原文，生产环境慎开；且敏感模式下强制不打印。
_DETAIL = os.getenv("PERF_TRACE_DETAIL", "false").strip().lower() not in (
    "false", "0", "no", "off",
)


def _safe_int_env(name: str, default: int) -> int:
    """读环境变量并转 int，非法/缺失回退 default（避免模块导入期崩溃）。"""
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


_MAX_DETAIL = _safe_int_env("PERF_TRACE_DETAIL_MAX", 2000)


def _safe_is_sensitive() -> bool:
    """框架敏感模式下应脱敏，不打印请求/响应原文。

    懒导入避免硬依赖；与 task_loop_event_executor 的 UserConfig.is_sensitive() 同源。
    """
    try:
        from openjiuwen.core.common.security.user_config import UserConfig
        return bool(UserConfig.is_sensitive())
    except Exception:
        return False


def _should_log_detail() -> bool:
    """是否打印请求/响应详情：需 DETAIL 开启且当前非敏感模式。"""
    return _DETAIL and not _safe_is_sensitive()


def _resolve_trace_id(ctx: Any) -> str:
    """拿请求级 trace_id。

    优先读 DeepAdapter 在 invoke 前设置的请求级 _request_context.request_id；
    回退到 ctx.inputs.conversation_id / session_id；最后兜底生成。
    """
    # 1. 请求级 _request_context（invoke 前由 DeepAdapter 设置）
    try:
        from jiuwenclaw.telemetry.instrumentors.telemetry_rail import _request_context
        rc = _request_context.get()
        if rc and rc.get("request_id"):
            return str(rc["request_id"])
    except Exception as exc:
        logger.debug("[perf] trace_id: request context unavailable, falling back: %s", exc)
    # 2. ctx.inputs.conversation_id / session_id（session 级兜底）
    try:
        inputs = getattr(ctx, "inputs", None)
        if inputs is not None:
            cid = (
                getattr(inputs, "conversation_id", None)
                or getattr(inputs, "session_id", None)
            )
            if cid:
                return str(cid)
    except Exception as exc:
        logger.debug("[perf] trace_id: inputs fallback unavailable: %s", exc)
    # 3. 兜底
    return f"perf_{time.monotonic_ns():x}"


def _resolve_session_id(ctx: Any) -> str:
    """拿会话级 session_id。优先读请求级 _request_context.session_id，
    回退 ctx.inputs.conversation_id / session_id。"""
    try:
        from jiuwenclaw.telemetry.instrumentors.telemetry_rail import _request_context
        rc = _request_context.get()
        if rc and rc.get("session_id"):
            return str(rc["session_id"])
    except Exception as exc:
        logger.debug("[perf] session_id: request context unavailable, falling back: %s", exc)
    try:
        inputs = getattr(ctx, "inputs", None)
        if inputs is not None:
            sid = (
                getattr(inputs, "conversation_id", None)
                or getattr(inputs, "session_id", None)
            )
            if sid:
                return str(sid)
    except Exception as exc:
        logger.debug("[perf] session_id: inputs fallback unavailable: %s", exc)
    return ""


def _log_kv() -> str:
    """返回日志前缀 key=value 串：trace_id=xxx session_id=yyy。"""
    return f"trace_id={_trace_id.get()} session_id={_session_id.get()}"


def _mark(phase: str, call_id: str = "") -> None:
    """记录某阶段开始时间。"""
    starts = _starts.get()
    if starts is None:
        starts = {}
        _starts.set(starts)
    starts[f"{phase}:{call_id}"] = time.perf_counter()


def _elapsed(phase: str, call_id: str = "") -> float:
    """取出并清除某阶段开始时间，返回耗时(ms)。找不到返回 -1.0。"""
    starts = _starts.get() or {}
    t0 = starts.pop(f"{phase}:{call_id}", None)
    if t0 is None:
        return -1.0
    return (time.perf_counter() - t0) * 1000.0


def _tool_info(ctx: Any) -> tuple[str, str]:
    """从 ctx.inputs 取 (tool_name, tool_call_id)。"""
    inputs = getattr(ctx, "inputs", None)
    if inputs is None:
        return "", ""
    tc = getattr(inputs, "tool_call", None)
    if tc is not None:
        return str(getattr(tc, "name", "") or ""), str(getattr(tc, "id", "") or "")
    return str(getattr(inputs, "tool_name", "") or ""), ""


def _truncate(s: Any, max_len: int | None = None) -> str:
    """截断长字符串，超长加 ...[+N] 标记。"""
    if s is None:
        return ""
    s = str(s)
    limit = max_len if max_len is not None else _MAX_DETAIL
    if len(s) <= limit:
        return s
    return s[:limit] + f"...[+{len(s) - limit}]"


def _last_user_text(messages: Any) -> str:
    """提取最后一条 role=user 消息的文本（当前用户提问），跳过 system/历史/tool。

    content 为多模态 list 时只拼 text 部分；无 user 消息返回 ""。
    """
    if not messages:
        return ""
    try:
        text = ""
        for msg in messages:
            role = getattr(msg, "role", None)
            if role is None and isinstance(msg, dict):
                role = msg.get("role", "")
            if role != "user":
                continue
            content = getattr(msg, "content", None)
            if content is None and isinstance(msg, dict):
                content = msg.get("content", "")
            if isinstance(content, list):
                text = "".join(
                    str(p.get("text", "")) if isinstance(p, dict) else str(p)
                    for p in content
                )
            else:
                text = str(content) if content is not None else ""
        return text
    except Exception:
        return ""


def _response_summary(resp: Any) -> str:
    """提取模型响应摘要：finish_reason + content + tool_calls + tokens。"""
    if resp is None:
        return "None"
    try:
        content = getattr(resp, "content", "") or ""
        finish = getattr(resp, "finish_reason", "") or ""
        tcs = getattr(resp, "tool_calls", None) or []
        tc_names = [getattr(tc, "name", "?") for tc in tcs] if tcs else []
        usage = getattr(resp, "usage_metadata", None)
        tok = ""
        if usage:
            tok = f" tokens={{in={getattr(usage, 'input_tokens', '?')},out={getattr(usage, 'output_tokens', '?')}}}"
        return f"finish={finish} content={_truncate(content)} tool_calls={tc_names}{tok}"
    except Exception:
        return "<unreadable>"


def _tool_args(inputs: Any) -> str:
    """提取 tool_call.arguments。"""
    tc = getattr(inputs, "tool_call", None)
    if tc is None:
        return ""
    args = getattr(tc, "arguments", None)
    args = args if args is not None else {}
    return _truncate(str(args))


def _tool_result_str(inputs: Any) -> str:
    """提取 tool_result。"""
    result = getattr(inputs, "tool_result", None)
    if result is None:
        return ""
    return _truncate(str(result))


def _hook_safe(method):
    """装饰器：吞掉钩子异常并计入熔断，避免回调框架 trigger() 兜底打 ERROR+traceback。

    钩子失败累计到阈值后熔断，后续钩子直接跳过（返回 None），
    把“钩子失败”的噪声限制在阈值条 WARNING 内（走项目 logger，不进框架 openjiuwen.* 树）。
    """
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        if getattr(self, "_degraded", False):
            return None
        try:
            return await method(self, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            failure_count = getattr(self, "_failure_count", 0) + 1
            setattr(self, "_failure_count", failure_count)
            failure_threshold = getattr(self, "_failure_threshold", 10)
            logger.warning(
                "[perf] hook %s failed (%d/%d): %s",
                method.__name__,
                failure_count,
                failure_threshold,
                exc,
            )
            if failure_count >= failure_threshold:
                setattr(self, "_degraded", True)
                logger.warning(
                    "[perf] circuit breaker tripped - PerfTraceRail hooks disabled until process restart"
                )
            return None

    return wrapper


class PerfTraceRail(DeepAgentRail):
    """性能追踪 Rail：统一 trace_id + 各阶段耗时日志（INFO 级，不依赖 OTel 后端）。

    回调框架按 priority 降序执行（高者先跑），且 before/after 同序、不反转；
    priority=5 属较低优先级，各阶段耗时可能包含更高优先级 rail 的 after 钩子开销
    （亚毫秒级，不影响定位）。
    before/after 钩子共用同一个 ctx 对象，靠 setattr 传递 call_id。
    """

    priority = 5

    def __init__(self) -> None:
        super().__init__()
        # 熔断：钩子失败累计到阈值后整体跳过，避免框架兜底打 ERROR+traceback 噪声
        self._failure_count: int = 0
        self._degraded: bool = False
        self._failure_threshold: int = _safe_int_env("PERF_TRACE_HOOK_FAILURE_THRESHOLD", 10)

    # ------------------------------------------------------------------
    # invoke - 请求端到端
    # ------------------------------------------------------------------
    @_hook_safe
    async def before_invoke(self, ctx: Any) -> None:
        if not _ENABLED:
            return
        tid = _resolve_trace_id(ctx)
        _trace_id.set(tid)
        _session_id.set(_resolve_session_id(ctx))
        _starts.set({})
        _iter.set(0)
        _mark("invoke")
        logger.info("[perf] %s phase=invoke start", _log_kv())

    @_hook_safe
    async def after_invoke(self, ctx: Any) -> None:
        if not _ENABLED:
            return
        logger.info(
            "[perf] %s phase=invoke end elapsed_ms=%.1f",
            _log_kv(), _elapsed("invoke"),
        )

    # ------------------------------------------------------------------
    # task_iteration - 每轮外层 task-loop 迭代（一轮内可能含多次 model_call）
    # ------------------------------------------------------------------
    @_hook_safe
    async def before_task_iteration(self, ctx: Any) -> None:
        if not _ENABLED:
            return
        n = _iter.get() + 1
        _iter.set(n)
        _mark("iter", str(n))
        logger.info("[perf] %s phase=iter start iter=%d", _log_kv(), n)

    @_hook_safe
    async def after_task_iteration(self, ctx: Any) -> None:
        if not _ENABLED:
            return
        n = _iter.get()
        logger.info(
            "[perf] %s phase=iter end iter=%d elapsed_ms=%.1f",
            _log_kv(), n, _elapsed("iter", str(n)),
        )

    # ------------------------------------------------------------------
    # model_call - LLM 调用
    # ------------------------------------------------------------------
    @_hook_safe
    async def before_model_call(self, ctx: Any) -> None:
        if not _ENABLED:
            return
        n = _iter.get()
        call_id = str(time.monotonic_ns())
        setattr(ctx, "_perf_model_call_id", call_id)
        _mark("model", call_id)
        logger.info("[perf] %s phase=model start iter=%d", _log_kv(), n)
        if _should_log_detail():
            inputs = getattr(ctx, "inputs", None)
            msgs = getattr(inputs, "messages", None) if inputs else None
            logger.info(
                "[perf] %s phase=model req iter=%d msgs=%d last_user=%s",
                _log_kv(), n, len(msgs) if msgs else 0, _truncate(_last_user_text(msgs)),
            )

    @_hook_safe
    async def after_model_call(self, ctx: Any) -> None:
        if not _ENABLED:
            return
        call_id = getattr(ctx, "_perf_model_call_id", "")
        logger.info(
            "[perf] %s phase=model end iter=%d elapsed_ms=%.1f",
            _log_kv(), _iter.get(), _elapsed("model", call_id),
        )
        if _should_log_detail():
            inputs = getattr(ctx, "inputs", None)
            resp = getattr(inputs, "response", None) if inputs else None
            logger.info(
                "[perf] %s phase=model resp iter=%d %s",
                _log_kv(), _iter.get(), _response_summary(resp),
            )

    # ------------------------------------------------------------------
    # tool_call - 工具调用（含 skill_step / skill_complete）
    # ------------------------------------------------------------------
    @_hook_safe
    async def before_tool_call(self, ctx: Any) -> None:
        if not _ENABLED:
            return
        tool_name, tool_call_id = _tool_info(ctx)
        call_id = tool_call_id or f"__{tool_name}_{time.monotonic_ns()}"
        setattr(ctx, "_perf_tool_call_id", call_id)
        setattr(ctx, "_perf_tool_name", tool_name)
        _mark("tool", call_id)
        logger.info(
            "[perf] %s phase=tool start tool=%s",
            _log_kv(), tool_name,
        )
        if _should_log_detail():
            inputs = getattr(ctx, "inputs", None)
            logger.info(
                "[perf] %s phase=tool req tool=%s args=%s",
                _log_kv(), tool_name, _tool_args(inputs),
            )

    @_hook_safe
    async def after_tool_call(self, ctx: Any) -> None:
        if not _ENABLED:
            return
        call_id = getattr(ctx, "_perf_tool_call_id", "")
        tool_name = getattr(ctx, "_perf_tool_name", "")
        # Fallback：before 没设（orphan after，如 force_finish / streaming tool 路径
        # 只触发 after 不触发 before，或 before/after 非同一 ctx），重新从 ctx.inputs 取
        if not call_id:
            tn, tcid = _tool_info(ctx)
            tool_name = tool_name or tn
            call_id = tcid
        elapsed = _elapsed("tool", call_id)
        if elapsed < 0:
            # 没有对应 before（orphan after），不打 end 日志，避免 elapsed_ms=-1 误导
            return
        logger.info(
            "[perf] %s phase=tool end tool=%s elapsed_ms=%.1f",
            _log_kv(), tool_name, elapsed,
        )
        if _should_log_detail():
            inputs = getattr(ctx, "inputs", None)
            logger.info(
                "[perf] %s phase=tool resp tool=%s result=%s",
                _log_kv(), tool_name, _tool_result_str(inputs),
            )


def register_rails() -> list[DeepAgentRail]:
    """AGENT_EXTRA_RAILS 注册入口：返回要挂载的 Rail 实例列表。"""
    return [PerfTraceRail()]
