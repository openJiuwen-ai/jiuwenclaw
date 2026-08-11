"""Bind explicit invocation context inside the real execution tasks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.common.invocation_context.codec import (
    INVOCATION_CONTEXT_EXTRA_KEY,
    invocation_context_from_dict,
)
from jiuwenswarm.common.invocation_context.models import InvocationContext
from jiuwenswarm.common.invocation_context.runtime import (
    get_current_invocation_context,
    reset_current_invocation_context,
    set_current_invocation_context,
)

logger = logging.getLogger(__name__)


_TASK_TOKEN_KEY = "__jiuwenswarm_invocation_task_token"
_INVOKE_TOKEN_KEY = "__jiuwenswarm_invocation_invoke_token"


def _extract_invocation_context(run_context: Any) -> InvocationContext | None:
    """Extract an InvocationContext from RunContext/dict/None."""

    if run_context is None:
        return None

    if isinstance(run_context, Mapping):
        extra = run_context.get("extra")
    else:
        extra = getattr(run_context, "extra", None)
    if not isinstance(extra, Mapping):
        return None

    payload = extra.get(INVOCATION_CONTEXT_EXTRA_KEY)
    if not isinstance(payload, Mapping):
        return None
    # The codec validates version/identity and returns detached mutable values.
    return invocation_context_from_dict(dict(payload))


def _run_context_from_callback(ctx: AgentCallbackContext) -> Any:
    """Read run context across agent-core versions.

    Current DeepAgent releases expose it as ``inputs.run_context``.  Older
    releases used a plain mapping or carried it in callback ``extra``; these
    fallbacks are read-only compatibility shims and do not become a request
    registry.
    """

    inputs = getattr(ctx, "inputs", None)
    run_context = getattr(inputs, "run_context", None)
    if run_context is not None:
        return run_context
    if isinstance(inputs, Mapping):
        run_context = inputs.get("run_context")
        if run_context is not None:
            return run_context
    extra = getattr(ctx, "extra", None)
    if isinstance(extra, Mapping):
        return extra.get("run_context")
    return None


def _task_id() -> int | None:
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return None
    return id(task) if task is not None else None


def _context_log_fields(context: InvocationContext) -> tuple[Any, ...]:
    return (
        context.invocation_id,
        context.request_id,
        context.session_id,
        context.channel_id,
        _task_id(),
    )


class InvocationContextRail(DeepAgentRail):
    """Bind/reset invocation context for outer and task-loop lifecycle hooks."""

    _TASK_TOKEN_KEY = _TASK_TOKEN_KEY
    _INVOKE_TOKEN_KEY = _INVOKE_TOKEN_KEY

    def __init__(self) -> None:
        super().__init__()

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        run_context = _run_context_from_callback(ctx)
        try:
            invocation = _extract_invocation_context(run_context)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "[INVOCATION_CTX] INVALID phase=BEFORE_INVOKE "
                "task_id=%s error=%s",
                _task_id(),
                exc,
            )
            return
        if invocation is None:
            logger.info("[INVOCATION_CTX] MISSING phase=BEFORE_INVOKE task_id=%s", _task_id())
            return

        token = set_current_invocation_context(invocation)
        extra = getattr(ctx, "extra", None)
        if not isinstance(extra, dict):
            # AgentCallbackContext owns a dict in supported core versions; be
            # defensive for lightweight test doubles.
            extra = {}
            try:
                ctx.extra = extra
            except Exception:
                logger.warning(
                    "[INVOCATION_CTX] INVALID phase=BEFORE_INVOKE "
                    "reason=context_extra_unavailable task_id=%s",
                    _task_id(),
                )
                reset_current_invocation_context(token)
                return
        extra[self._INVOKE_TOKEN_KEY] = token
        logger.info(
            "[INVOCATION_CTX] BEFORE_INVOKE_BIND invocation_id=%s request_id=%s "
            "session_id=%s channel_id=%s asyncio_task_id=%s",
            *_context_log_fields(invocation),
        )

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        extra = getattr(ctx, "extra", None)
        token = extra.pop(self._INVOKE_TOKEN_KEY, None) if isinstance(extra, dict) else None
        if token is None:
            return
        invocation = get_current_invocation_context()
        try:
            reset_current_invocation_context(token)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "[INVOCATION_CTX] INVALID phase=AFTER_INVOKE_RESET "
                "task_id=%s error=%s",
                _task_id(),
                exc,
            )
            return
        if invocation is not None:
            logger.info(
                "[INVOCATION_CTX] AFTER_INVOKE_RESET invocation_id=%s request_id=%s "
                "session_id=%s channel_id=%s asyncio_task_id=%s",
                *_context_log_fields(invocation),
            )
        else:
            logger.info(
                "[INVOCATION_CTX] AFTER_INVOKE_RESET asyncio_task_id=%s",
                _task_id(),
            )

    async def before_task_iteration(self, ctx: AgentCallbackContext) -> None:
        run_context = _run_context_from_callback(ctx)
        try:
            invocation = _extract_invocation_context(run_context)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "[INVOCATION_CTX] INVALID phase=BEFORE_TASK_BIND "
                "task_id=%s error=%s",
                _task_id(),
                exc,
            )
            return
        if invocation is None:
            logger.info("[INVOCATION_CTX] MISSING phase=BEFORE_TASK_BIND task_id=%s", _task_id())
            return

        token = set_current_invocation_context(invocation)
        extra = getattr(ctx, "extra", None)
        if not isinstance(extra, dict):
            extra = {}
            try:
                ctx.extra = extra
            except Exception:
                logger.warning(
                    "[INVOCATION_CTX] INVALID phase=BEFORE_TASK_BIND "
                    "reason=context_extra_unavailable task_id=%s",
                    _task_id(),
                )
                reset_current_invocation_context(token)
                return
        extra[self._TASK_TOKEN_KEY] = token
        logger.info(
            "[INVOCATION_CTX] BEFORE_TASK_BIND invocation_id=%s request_id=%s "
            "session_id=%s channel_id=%s asyncio_task_id=%s",
            *_context_log_fields(invocation),
        )

    async def after_task_iteration(self, ctx: AgentCallbackContext) -> None:
        extra = getattr(ctx, "extra", None)
        token = extra.pop(self._TASK_TOKEN_KEY, None) if isinstance(extra, dict) else None
        if token is None:
            return
        invocation = get_current_invocation_context()
        try:
            reset_current_invocation_context(token)
        except (RuntimeError, ValueError) as exc:
            logger.warning(
                "[INVOCATION_CTX] INVALID phase=AFTER_TASK_RESET "
                "task_id=%s error=%s",
                _task_id(),
                exc,
            )
            return
        if invocation is not None:
            logger.info(
                "[INVOCATION_CTX] AFTER_TASK_RESET invocation_id=%s request_id=%s "
                "session_id=%s channel_id=%s asyncio_task_id=%s",
                *_context_log_fields(invocation),
            )
        else:
            logger.info(
                "[INVOCATION_CTX] AFTER_TASK_RESET asyncio_task_id=%s",
                _task_id(),
            )


__all__ = [
    "InvocationContextRail",
    "_extract_invocation_context",
]
