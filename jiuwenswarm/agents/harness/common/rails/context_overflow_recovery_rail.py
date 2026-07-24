# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import re
from typing import Any

from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
    FullCompactProcessor,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.common.logging import logger


# 413 恢复时 threshold_override 占模型窗口的比例
# 0.85 表示压缩后上下文最多占模型窗口的 85%，预留 15% 给 LLM 输出
RECOVERY_THRESHOLD_RATIO = 0.85


def _parse_token_limits(exc: Exception) -> tuple[int | None, int | None]:
    """从 413 错误解析 limit_tokens 和 actual_tokens。

    Returns: (actual_tokens, limit_tokens)
    """
    msg = str(exc)

    # Anthropic: "prompt is too long: N tokens > M maximum"
    m = re.search(r'(\d+)\s*tokens?\s*>\s*(\d+)', msg, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))

    # 华为: "prompt length N must less than maximum input length M"
    m = re.search(r'prompt length\s+(\d+).*?maximum input length\s+(\d+)', msg, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))

    # OpenAI: "maximum context length is N"
    m = re.search(r'maximum context length is\s+(\d+)', msg, re.IGNORECASE)
    if m:
        return None, int(m.group(1))

    return None, None


class ContextOverflowRecoveryRail(DeepAgentRail):
    """Context overflow recovery chain (reactive + proactive bridge).

    **Reactive** — when an LLM call fails with a context-overflow error (413, 400 with
    "context_length_exceeded", etc.):
    1. **Detect** — status_code + keyword matching on the exception.
    2. **FullCompact** — set ``force_compact`` and optional ``threshold_override``
       (``limit * 0.85``) so the next ``get_context_window`` shrinks harder.
    3. **Retry** — call ``ctx.request_retry()`` to re-enter the LLM call.
    4. **Circuit-break** — after ``max_recovery_attempts`` consecutive failures,
       stop retrying and emit a clear user-facing message.

    **Proactive bridge** — when ``FullCompactProcessor`` whole-window fallback still
    exceeds the hard window, it defers to this recovery chain (setting the
    ``deferred_overflow_recovery`` / ``force_compact`` flags) instead of failing.
    ``before_model_call`` consumes those flags and re-asserts force-compact so the
    upcoming ``get_context_window`` can shrink further before the LLM is invoked.

    Priority: 100 (higher value = runs first; higher than StreamEventRail's 80
    and ContextProcessorRail), so ``on_model_exception`` fires **before**
    those rails.
    """

    priority = 100

    # Per-session overflow streak. Shared adapter-level rail instances serve
    # concurrent sessions; a scalar counter would cross-contaminate them.
    _SID_KEY = "__jiuwenswarm_overflow_recovery_session_id__"
    _STREAM_SID_KEY = "__jiuwenswarm_session_id__"

    def __init__(self, max_recovery_attempts: int = 2) -> None:
        super().__init__()
        self._max_recovery_attempts = max_recovery_attempts
        self._consecutive_overflow_counts: dict[str, int] = {}
        self._logged_missing_full_compact_bridge_api = False

    def cleanup_session(self, session_id: str = "") -> None:
        """Remove per-session overflow counter for *session_id*."""
        sid = session_id or "default"
        self._consecutive_overflow_counts.pop(sid, None)

    def _resolve_sid(self, ctx: AgentCallbackContext) -> str:
        """Resolve the per-session key used by overflow recovery counting."""
        sid = ctx.extra.get(self._SID_KEY)
        if isinstance(sid, str) and sid:
            return sid

        stream_sid = ctx.extra.get(self._STREAM_SID_KEY)
        if isinstance(stream_sid, str) and stream_sid:
            ctx.extra[self._SID_KEY] = stream_sid
            return stream_sid

        session = getattr(ctx, "session", None)
        if session is not None:
            get_session_id = getattr(session, "get_session_id", None)
            if callable(get_session_id):
                try:
                    resolved = str(get_session_id() or "").strip()
                except Exception:
                    resolved = ""
                if resolved:
                    ctx.extra[self._SID_KEY] = resolved
                    return resolved
            resolved = str(getattr(session, "session_id", "") or "").strip()
            if resolved:
                ctx.extra[self._SID_KEY] = resolved
                return resolved

        ctx.extra[self._SID_KEY] = "default"
        return "default"

    def _get_overflow_count(self, sid: str) -> int:
        return self._consecutive_overflow_counts.get(sid, 0)

    def _set_overflow_count(self, sid: str, count: int) -> None:
        if count <= 0:
            self._consecutive_overflow_counts.pop(sid, None)
        else:
            self._consecutive_overflow_counts[sid] = count

    # ------------------------------------------------------------------
    # before_model_call: bridge proactive compression deferral
    # ------------------------------------------------------------------

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Run recovery prep when proactive whole-window compact deferred overflow."""
        if ctx.context is None:
            return

        processor = self._find_full_compact_processor(ctx.context)
        if processor is None:
            return

        consume = getattr(processor, "consume_deferred_overflow_recovery", None)
        pending_getter = getattr(processor, "is_force_compact_pending", None)
        if not callable(consume) or not callable(pending_getter):
            self._warn_missing_bridge_api_once(processor, consume, pending_getter)
            return

        deferred = bool(consume())
        force_pending = bool(pending_getter())

        if not deferred:
            # force_pending alone means force_compact is already armed (reactive
            # 413 path or agent-core). Do not re-resolve threshold_override —
            # on_model_exception may have set it from the real model limit.
            if force_pending:
                logger.info(
                    "[ContextOverflowRecovery] force_compact already pending before "
                    "model call; skipping threshold re-resolution"
                )
            return

        logger.info(
            "[ContextOverflowRecovery] Proactive overflow recovery before model call "
            "(deferred=%s force_compact_pending=%s)",
            deferred,
            force_pending,
        )
        await self._run_overflow_recovery_prep(ctx)

    # ------------------------------------------------------------------
    # on_model_exception: core recovery logic
    # ------------------------------------------------------------------

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        exc = ctx.exception
        if exc is None or not self._is_context_overflow_error(exc):
            return

        sid = self._resolve_sid(ctx)
        count = self._get_overflow_count(sid) + 1
        self._set_overflow_count(sid, count)
        actual_tokens, limit_tokens = _parse_token_limits(exc)
        logger.warning(
            "[ContextOverflowRecovery] Context overflow detected "
            "(session=%s attempt %d/%d) actual_tokens=%s limit_tokens=%s",
            sid,
            count,
            self._max_recovery_attempts,
            actual_tokens,
            limit_tokens,
        )

        if count > self._max_recovery_attempts:
            await self._circuit_break(ctx)
            return

        threshold_override = self._resolve_threshold_override(ctx, limit_tokens)
        logger.warning(
            "[ContextOverflowRecovery] Calculate threshold_override = %s",
            threshold_override,
        )
        compact_success = self._set_force_compact_flag(ctx, threshold_override=threshold_override)

        if compact_success:
            ctx.request_retry(delay_seconds=0)
            logger.info("[ContextOverflowRecovery] Recovery actions taken, requesting retry")
        else:
            logger.warning(
                "[ContextOverflowRecovery] Could not set force_compact flag or compact context failed; "
                "retrying anyway (session=%s attempt %d/%d threshold_override=%s)",
                sid,
                count,
                self._max_recovery_attempts,
                threshold_override,
            )
            ctx.request_retry(delay_seconds=0)

    # ------------------------------------------------------------------
    # after_model_call: reset counter on success
    # ------------------------------------------------------------------

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        if ctx.exception is None:
            sid = self._resolve_sid(ctx)
            count = self._get_overflow_count(sid)
            if count > 0:
                logger.info(
                    "[ContextOverflowRecovery] LLM call succeeded after %d overflow "
                    "recovery attempt(s) (session=%s)",
                    count,
                    sid,
                )
            self._set_overflow_count(sid, 0)

    # ------------------------------------------------------------------
    # Detection: structured → status code → keyword fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _is_context_overflow_error(exc: Exception) -> bool:
        """判断异常是否为上下文溢出错误。
        1. status_code=413 — 明确的溢出语义，直接判定
        2. status_code=400 + 溢出关键词 — 400 状态码 对应多种错误类型（参数错误、权限错误等），必须结合关键词,
        才能区分出上下文溢出，避免误判
        3. 无 status_code + 溢出关键词兜底 — 非 SDK 标准异常的纯文本匹配
        """
        status_code = getattr(exc, "status_code", None)
        exc_str = str(exc)
        error_message_lower = exc_str.lower()

        overflow_keys = (
            "prompt is too long",       # Anthropic 精确前缀
            "input too long",           # Anthropic 新格式
            "context_length_exceeded",  # OpenAI 标准 error code
            "maximum context length",   # OpenAI: "maximum context length is N"
            "maximum input length",     # 华为: "maximum input length M"
            "must less than the maximum input",
            "context length exceeded"
        )
        has_overflow_keyword = any(keyword in error_message_lower for keyword in overflow_keys)

        if status_code == 413 or (status_code == 400 and has_overflow_keyword):
            return True

        # 无 status_code 但有溢出关键词
        if status_code is None and has_overflow_keyword:
            return True

        # Compare against lowercased haystack with lowercased needles.
        if (
            "error code: 400" in error_message_lower
            and "modelarts.81001" in error_message_lower
            and has_overflow_keyword
        ):
            return True

        return False

    async def _run_overflow_recovery_prep(self, ctx: AgentCallbackContext) -> bool:
        # Proactive deferral has no 413 limit string; fall back to hard-window * ratio.
        threshold_override = self._resolve_threshold_override(ctx, None)
        return self._set_force_compact_flag(ctx, threshold_override=threshold_override)

    @staticmethod
    def _find_full_compact_processor(context: Any) -> Any | None:
        if context is None:
            logger.warning("[ContextOverflowRecovery] No context available when finding FullCompactProcessor")
            return None

        processors = getattr(context, "_processors", None)
        if not processors:
            logger.warning("[ContextOverflowRecovery] Context has no _processors when finding FullCompactProcessor")
            return None
        for processor in processors:
            if isinstance(processor, FullCompactProcessor):
                return processor
        logger.warning(
            "[ContextOverflowRecovery] No FullCompactProcessor found in context processors: %s",
            [type(processor).__name__ for processor in processors],
        )
        return None

    def _warn_missing_bridge_api_once(self, processor: Any, consume: Any, pending_getter: Any) -> None:
        if self._logged_missing_full_compact_bridge_api:
            return
        missing = []
        if not callable(consume):
            missing.append("consume_deferred_overflow_recovery")
        if not callable(pending_getter):
            missing.append("is_force_compact_pending")
        logger.warning(
            "[ContextOverflowRecovery] FullCompactProcessor is missing proactive overflow bridge API %s; "
            "agent-core version may be incompatible and deferred recovery bridge is disabled",
            missing,
        )
        self._logged_missing_full_compact_bridge_api = True

    # ------------------------------------------------------------------
    # threshold_override resolution
    # ------------------------------------------------------------------

    @classmethod
    def _resolve_threshold_override(cls, ctx: AgentCallbackContext, parsed_limit: int | None) -> int | None:
        """Compute threshold_override for FullCompact adaptive chain.

        threshold_override = model_limit * RECOVERY_THRESHOLD_RATIO
        自适应模型窗口大小，按照比例设置压缩阈值。
        """
        if parsed_limit is not None:
            return int(parsed_limit * RECOVERY_THRESHOLD_RATIO)

        processor = cls._find_full_compact_processor(getattr(ctx, "context", None))
        hard_window = getattr(processor, "hard_window_tokens", None) if processor is not None else None
        if isinstance(hard_window, int) and hard_window > 0:
            return int(hard_window * RECOVERY_THRESHOLD_RATIO)

        # No parsed limit / hard window — FullCompactProcessor keeps trigger_total_tokens.
        logger.info(
            "[ContextOverflowRecovery] No limit parsed and no hard_window_tokens; "
            "FullCompactProcessor will use default trigger_total_tokens"
        )
        return None

    # ------------------------------------------------------------------
    # Set force_compact flag on FullCompactProcessor
    # ------------------------------------------------------------------

    def _set_force_compact_flag(self, ctx: AgentCallbackContext, *, threshold_override: int | None = None) -> bool:
        """Set force_compact flag on the FullCompactProcessor attached to context."""
        processor = self._find_full_compact_processor(ctx.context)
        if processor is None:
            return False

        set_force_compact = getattr(processor, "set_force_compact", None)
        if not callable(set_force_compact):
            logger.warning(
                "[ContextOverflowRecovery] FullCompactProcessor has no set_force_compact method"
            )
            return False

        set_force_compact(True)
        logger.info(
            "[ContextOverflowRecovery] Set force_compact=True on %s",
            type(processor).__name__,
        )
        set_threshold_override = getattr(processor, "set_overflow_threshold_override", None)
        if threshold_override is not None and callable(set_threshold_override):
            set_threshold_override(threshold_override)
            logger.info(
                "[ContextOverflowRecovery] Set force_compact=True and "
                "threshold_override=%s on %s",
                threshold_override,
                type(processor).__name__,
            )
        return True

    # ------------------------------------------------------------------
    # Circuit breaker: overflow recovery exhausted
    # ------------------------------------------------------------------

    async def _circuit_break(self, ctx: AgentCallbackContext) -> None:
        sid = self._resolve_sid(ctx)
        count = self._get_overflow_count(sid)
        logger.error(
            "[ContextOverflowRecovery] Circuit breaker triggered after %d "
            "consecutive context overflow errors (session=%s). Will not retry.",
            count,
            sid,
        )

        session = getattr(ctx, "session", None)
        if session is not None and hasattr(session, "write_stream"):
            try:
                from openjiuwen.core.session.stream import OutputSchema
                await session.write_stream(
                    OutputSchema(
                        type="error",
                        payload={
                            "error_type": "context_overflow_circuit_break",
                            "message": (
                                f"上下文持续溢出（连续 {count} 次），"
                                "自动压缩恢复失败。建议：/compact 手动压缩上下文，或开始新会话。"
                            ),
                            "consecutive_errors": count,
                        },
                    )
                )
            except Exception as e:
                logger.warning("[ContextOverflowRecovery] Failed to send circuit-break event: %s", e)
        # Always clear so the next user turn can attempt recovery again.
        self._set_overflow_count(sid, 0)
