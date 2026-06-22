# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, Optional

from openjiuwen.core.context_engine.processor.offloader.message_summary_offloader import (
    CONTEXT_OVERFLOW_KEYWORDS,
)
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.common.logging import logger

_CONTEXT_OVERFLOW_RECOVERY_KEYWORDS = CONTEXT_OVERFLOW_KEYWORDS


class ContextOverflowRecoveryRail(DeepAgentRail):
    """API 413 / context overflow reactive recovery chain.
    When an LLM call fails with a context-overflow error (413, 400 with
    "context_length_exceeded", etc.), this rail executes a cascade:
    1. **Detect** — keyword matching on the exception message.
    2. **SessionMemory** — force an incremental notes update (bypass trigger_tokens).
    3. **FullCompact** — set ``force_compact`` flag so the next ``get_context_window``
       unconditionally triggers FullCompact (bypass ``trigger_total_tokens``).
    4. **Retry** — call ``ctx.request_retry()`` to re-enter the LLM call.
    5. **Circuit-break** — after ``max_recovery_attempts`` consecutive failures,
       stop retrying and let the exception propagate with a clear user-facing message.

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

        await self._force_session_memory_update(ctx)

        compact_success = self._set_force_compact_flag(ctx)

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
        """Set force_compact flag on FullCompactProcessor via context._processors.

        Note: ``SessionModelContext`` stores processors in ``self._processors``
        directly (not through an intermediate ``_context_engine`` object).
        We iterate the list to find the FullCompactProcessor instance.
        """
        context = ctx.context
        if context is None:
            return False

        processors = getattr(context, "_processors", None)
        if processors is None:
            logger.debug("[ContextOverflowRecovery] No _processors on context")
            return False

        for processor in processors:
            cls_name = type(processor).__name__
            if "FullCompactProcessor" == cls_name and hasattr(processor, "set_force_compact"):
                processor.set_force_compact(True)
                logger.info(
                    "[ContextOverflowRecovery] Set force_compact=True on %s",
                    cls_name,
                )
                return True

        logger.warning("[ContextOverflowRecovery] No FullCompactProcessor found in context _processors")
        return False

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
