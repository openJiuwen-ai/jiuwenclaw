# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, Optional

from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
    FullCompactProcessor,
)
from openjiuwen.core.context_engine.processor.offloader.message_summary_offloader import (
    CONTEXT_OVERFLOW_KEYWORDS,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.common.logging import logger

_CONTEXT_OVERFLOW_RECOVERY_KEYWORDS = CONTEXT_OVERFLOW_KEYWORDS


class ContextOverflowRecoveryRail(DeepAgentRail):
    """Context overflow recovery chain (reactive + proactive bridge).

    **Reactive** — when an LLM call fails with a context-overflow error (413, 400 with
    "context_length_exceeded", etc.):
    1. **Detect** — keyword matching on the exception message.
    2. **SessionMemory** — force an incremental notes update (bypass trigger_tokens).
    3. **FullCompact** — set ``force_compact`` flag so the next ``get_context_window``
       unconditionally triggers FullCompact (bypass ``trigger_total_tokens``).
    4. **Retry** — call ``ctx.request_retry()`` to re-enter the LLM call.
    5. **Circuit-break** — after ``max_recovery_attempts`` consecutive failures,
       stop retrying and let the exception propagate with a clear user-facing message.

    **Proactive bridge** — when ``FullCompactProcessor`` whole-window fallback still
    exceeds the hard window, it defers to this recovery chain (setting the
    ``deferred_overflow_recovery`` / ``force_compact`` flags) instead of failing.
    ``before_model_call`` consumes those flags and runs the same session-memory +
    force-compact preparation so the upcoming ``get_context_window`` can shrink further
    before the LLM is invoked (or before a reactive retry after API overflow).

    Priority: 100 (higher value = runs first; higher than StreamEventRail's 80
    and ContextEngineeringRail's 85), so ``on_model_exception`` fires **before**
    those rails.
    """

    priority = 100

    def __init__(self, max_recovery_attempts: int = 3) -> None:
        super().__init__()
        self._max_recovery_attempts = max_recovery_attempts
        self._consecutive_overflow_count: int = 0
        self._context_engineering_rail: Optional[Any] = None
        self._agent: Optional[Any] = None
        self._logged_missing_full_compact_bridge_api = False

    def init(self, agent: Any) -> None:
        self._agent = agent

    def _ensure_context_engineering_rail(self) -> Optional[Any]:
        """Lazily find ContextEngineeringRail from agent.rails.

        CE Rail is mounted on-demand by ``_update_rails_for_mode`` which
        runs *after* ``init()``, so we cannot find it during init.
        Instead, resolve it on first use and cache the result.
        """
        if self._context_engineering_rail is not None:
            return self._context_engineering_rail
        if self._agent is None:
            return None
        rails = getattr(self._agent, "rails", None) or []
        for rail in rails:
            cls_name = type(rail).__name__
            if "ContextEngineeringRail" == cls_name:
                self._context_engineering_rail = rail
                logger.info("[ContextOverflowRecovery] Found ContextEngineeringRail: %s", cls_name)
                return rail
        logger.debug("[ContextOverflowRecovery] No ContextEngineeringRail found in agent rails")
        return None

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

        if not deferred and not force_pending:
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

        self._consecutive_overflow_count += 1
        logger.warning(
            "[ContextOverflowRecovery] Context overflow detected "
            "(attempt %d/%d): %s",
            self._consecutive_overflow_count,
            self._max_recovery_attempts,
            str(exc)[:300],
        )

        if self._consecutive_overflow_count > self._max_recovery_attempts:
            await self._circuit_break(ctx)
            return

        compact_success = await self._run_overflow_recovery_prep(ctx)

        if compact_success:
            ctx.request_retry(delay_seconds=0)
            logger.info(
                "[ContextOverflowRecovery] Recovery actions taken, requesting retry "
                "(attempt %d/%d)",
                self._consecutive_overflow_count,
                self._max_recovery_attempts,
            )
        else:
            logger.warning(
                "[ContextOverflowRecovery] Could not set force_compact flag; "
                "retrying anyway (attempt %d/%d)",
                self._consecutive_overflow_count,
                self._max_recovery_attempts,
            )
            ctx.request_retry(delay_seconds=0)

    # ------------------------------------------------------------------
    # after_model_call: reset counter on success
    # ------------------------------------------------------------------

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        if ctx.exception is None:
            if self._consecutive_overflow_count > 0:
                logger.info(
                    "[ContextOverflowRecovery] LLM call succeeded after %d overflow recovery attempt(s)",
                    self._consecutive_overflow_count,
                )
            self._consecutive_overflow_count = 0

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_context_overflow_error(exc: Exception) -> bool:
        error_message = str(exc).lower()
        return any(keyword in error_message for keyword in _CONTEXT_OVERFLOW_RECOVERY_KEYWORDS)

    async def _run_overflow_recovery_prep(self, ctx: AgentCallbackContext) -> bool:
        await self._force_session_memory_update(ctx)
        return self._set_force_compact_flag(ctx)

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
    # Step 1: Force SessionMemory update
    # ------------------------------------------------------------------

    async def _force_session_memory_update(self, ctx: AgentCallbackContext) -> None:
        ce_rail = self._ensure_context_engineering_rail()
        if ce_rail is None:
            logger.debug("[ContextOverflowRecovery] No CE rail, skipping session memory force update")
            return

        session_memory_mgr = ce_rail.get_session_memory_mgr()
        session_memory_enabled = ce_rail.session_memory_enabled()
        if not session_memory_enabled or session_memory_mgr is None:
            logger.debug("[ContextOverflowRecovery] SessionMemory not enabled, skipping force update")
            return

        workspace = ce_rail.get_workspace()
        if workspace is None:
            logger.debug("[ContextOverflowRecovery] No workspace on CE rail, skipping force update")
            return

        try:
            await session_memory_mgr.force_schedule_update(ctx, workspace=workspace)
            logger.info("[ContextOverflowRecovery] Forced session memory update scheduled")
        except Exception as e:
            logger.warning("[ContextOverflowRecovery] Failed to force session memory update: %s", e)

    # ------------------------------------------------------------------
    # Step 2: Set force_compact flag on FullCompactProcessor
    # ------------------------------------------------------------------

    def _set_force_compact_flag(self, ctx: AgentCallbackContext) -> bool:
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
        return True

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    async def _circuit_break(self, ctx: AgentCallbackContext) -> None:
        logger.error(
            "[ContextOverflowRecovery] Circuit breaker triggered after %d "
            "consecutive context overflow errors. Will not retry.",
            self._consecutive_overflow_count,
        )

        session = ctx.session
        if session is not None and hasattr(session, "write_stream"):
            try:
                from openjiuwen.core.session.stream import OutputSchema
                await session.write_stream(
                    OutputSchema(
                        type="error",
                        payload={
                            "error_type": "context_overflow_circuit_break",
                            "message": (
                                f"上下文持续溢出（连续 {self._consecutive_overflow_count} 次），"
                                "自动压缩恢复失败。建议：/compact 手动压缩上下文，或开始新会话。"
                            ),
                            "consecutive_errors": self._consecutive_overflow_count,
                        },
                    )
                )
                self._consecutive_overflow_count = 0
            except Exception as e:
                logger.warning("[ContextOverflowRecovery] Failed to send circuit-break event: %s", e)
