# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime patch: prevent ReAct from hanging forever on stuck tool tasks."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger("jiuwenswarm.openjiuwen_streaming_tool_patch")

_STREAMING_TOOL_WAIT_TIMEOUT_PATCHED = False

# Default wall-clock budget for StreamingToolExecutor.wait_all().
# Set STREAMING_TOOL_WAIT_TIMEOUT_S=0 (or negative) to disable and keep upstream behavior.
# Prefer agent-core DEFAULT_WAIT_ALL_TIMEOUT_S (180) when openjiuwen already ships timeout.
_DEFAULT_STREAMING_TOOL_WAIT_TIMEOUT_S = 180.0


def _resolve_streaming_tool_wait_timeout_s(
    *,
    default: float = _DEFAULT_STREAMING_TOOL_WAIT_TIMEOUT_S,
) -> Optional[float]:
    """Return wait_all timeout seconds, or None to disable."""
    raw = os.environ.get("STREAMING_TOOL_WAIT_TIMEOUT_S", str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return None
    return value


def apply_streaming_tool_wait_timeout_patch() -> None:
    """Prevent ReAct from hanging forever on stuck tool tasks.

    Prefer native agent-core ``StreamingToolExecutor.wait_all(timeout=...)``.
    Only monkey-patch when the installed openjiuwen build has no timeout.

    Env ``STREAMING_TOOL_WAIT_TIMEOUT_S`` overrides the default (180s) when
    the shim is active; ``<=0`` disables.
    """
    global _STREAMING_TOOL_WAIT_TIMEOUT_PATCHED
    if _STREAMING_TOOL_WAIT_TIMEOUT_PATCHED:
        return
    try:
        from openjiuwen.core.operator import streaming_tool_executor as ste  # type: ignore
    except ImportError:
        return

    _orig_wait_all = ste.StreamingToolExecutor.wait_all

    # Native agent-core already implements timeout — do not wrap again.
    if "timeout" in getattr(_orig_wait_all, "__code__", type("_", (), {"co_varnames": ()})).co_varnames:
        native_default = getattr(ste, "DEFAULT_WAIT_ALL_TIMEOUT_S", None)
        _STREAMING_TOOL_WAIT_TIMEOUT_PATCHED = True
        logger.info(
            "StreamingToolExecutor.wait_all timeout already native "
            "(DEFAULT_WAIT_ALL_TIMEOUT_S=%s); skip shim",
            native_default,
        )
        return

    async def _patched_wait_all(self, timeout=...):
        # Ellipsis => resolve from env (default 180s). Explicit None => no timeout.
        # Uses only public cancel_all / wait_all; remaps CancelledError
        # from cancel-on-timeout to TimeoutError for the ReAct loop.
        if timeout is ...:
            timeout = _resolve_streaming_tool_wait_timeout_s()
        if timeout is None:
            return await _orig_wait_all(self)

        try:
            return await asyncio.wait_for(_orig_wait_all(self), timeout=float(timeout))
        except asyncio.TimeoutError:
            logger.warning(
                "StreamingToolExecutor wait_all timed out after %.1fs; "
                "cancelling pending tools",
                float(timeout),
            )
            self.cancel_all()
            grace = min(5.0, float(timeout))
            try:
                results = await asyncio.wait_for(_orig_wait_all(self), timeout=grace)
            except asyncio.TimeoutError:
                logger.warning(
                    "StreamingToolExecutor tool task(s) still running "
                    "after cancel grace=%.1fs; draining via wait_all",
                    grace,
                )
                results = await _orig_wait_all(self)

            remapped = []
            for tool_call, value in results:
                name = getattr(tool_call, "name", "?")
                if isinstance(value, asyncio.CancelledError):
                    remapped.append(
                        (
                            tool_call,
                            TimeoutError(f"Tool timed out after {timeout}s: {name}"),
                        )
                    )
                else:
                    remapped.append((tool_call, value))
            return remapped

    ste.StreamingToolExecutor.wait_all = _patched_wait_all  # type: ignore[method-assign]
    _STREAMING_TOOL_WAIT_TIMEOUT_PATCHED = True
    logger.info(
        "StreamingToolExecutor.wait_all timeout patch applied "
        "(STREAMING_TOOL_WAIT_TIMEOUT_S default=%s)",
        _DEFAULT_STREAMING_TOOL_WAIT_TIMEOUT_S,
    )
