# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""RecentToolResultsRail — record & inject latest tool-call results.

Parent agent records the latest 3 tool-call results (full, untruncated) into
session state after each ``after_tool_call`` but does NOT inject them into its
own system prompt (``before_model_call`` is a no-op for the parent).

Child agent does NOT record its own tool calls (``after_tool_call`` is a
no-op for the child) and only injects the parent's latest 3 results into its
system prompt via ``ctx.extra["environment_context"]`` before each
``before_model_call``.

Rules:
- Parent rail: ``parent_session`` is ``None``; records to own session;
  ``before_model_call`` is a no-op (does not inject into own prompt).
- Child rail: ``parent_session`` set at construction; ``after_tool_call`` is
  a no-op (does not record own calls); ``before_model_call`` reads parent's
  latest 3 results and injects them into child's prompt.
- Child never writes back to parent; parent never reads child.
- Only whitelisted tools (default: ``bash``) are recorded by the parent;
  non-whitelisted tools are skipped.
- Failed tool calls are recorded with ``status="failed"`` and ``error=str(exc)``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)
from openjiuwen.core.session.recent_tool_results import (
    get_recent_results,
    record_tool_result,
)

logger = logging.getLogger(__name__)

DEFAULT_WHITELIST: frozenset[str] = frozenset({
    "bash",
})


class RecentToolResultsRail(DeepAgentRail):
    """Rail that maintains a 3-entry ring buffer of recent tool-call results
    and injects them into the child agent's system prompt before each LLM call.

    Args:
        whitelist: Tool names to record.  Only whitelisted tools are
            recorded; all others are skipped.  Defaults to
            ``DEFAULT_WHITELIST`` (bash).
        parent_session: Parent session reference for child-agent rails.
            When set, ``after_tool_call`` is a no-op (child does not record),
            and ``before_model_call`` reads the parent's latest 3 results
            and injects them into the child's prompt.
            When ``None`` (parent agent), ``after_tool_call`` records to own
            session, and ``before_model_call`` is a no-op (parent does not
            inject into own prompt).
    """

    priority = 150

    def __init__(
        self,
        whitelist: Optional[frozenset[str]] = None,
        parent_session: Any = None,
    ) -> None:
        super().__init__()
        self._whitelist: frozenset[str] = (
            whitelist if whitelist is not None else DEFAULT_WHITELIST
        )
        self._parent_session = parent_session

    # ------------------------------------------------------------------
    # after_tool_call — record to own session
    # ------------------------------------------------------------------

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Record the just-completed tool call into own session state.

        Child agent (parent_session is not None): no-op, does not record.
        Parent agent: records whitelisted tool calls to own session.
        """
        # Child agent does not record own calls
        if self._parent_session is not None:
            return

        if not isinstance(ctx.inputs, ToolCallInputs):
            return

        tool_name: str = ctx.inputs.tool_name or ""
        if tool_name not in self._whitelist:
            logger.debug(
                "[RecentToolResultsRail] skip non-whitelisted tool=%s",
                tool_name,
            )
            return

        entry = _build_entry(ctx)
        record_tool_result(ctx.session, entry)

    # ------------------------------------------------------------------
    # before_model_call — inject into system prompt
    # ------------------------------------------------------------------

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject recent results into system prompt via
        ``ctx.extra['environment_context']``.

        Parent agent (parent_session is None): no-op, does not inject into own prompt.
        Child agent: reads parent's latest 3 results and injects into child's prompt.

        The framework pops ``environment_context`` after each model call,
        so injection is per-round and never accumulates.
        """
        # Parent agent does not inject into own prompt
        if self._parent_session is None:
            return

        # Child agent: read parent's latest 3 results only
        parent_entries = get_recent_results(self._parent_session)
        if not parent_entries:
            return

        section = _format_results_section(parent_entries)
        if not section:
            return

        ctx.extra.setdefault("environment_context", []).append(
            {
                "content": section,
                "source": "RecentToolResultsRail",
            }
        )
        logger.debug(
            "[RecentToolResultsRail] inject parent=%d",
            len(parent_entries),
        )


# ======================================================================
# Helpers
# ======================================================================

def _build_entry(ctx: AgentCallbackContext) -> dict:
    """Build a result-entry dict from the current tool-call context."""
    inputs: ToolCallInputs = ctx.inputs
    tool_name: str = inputs.tool_name or ""
    tool_args: Any = inputs.tool_args
    tool_result: Any = inputs.tool_result

    is_failed: bool = ctx.exception is not None
    error_str: Optional[str] = str(ctx.exception) if is_failed else None
    result_str: Optional[str] = None if is_failed else _serialize_result(tool_result)

    return {
        "tool": tool_name,
        "args": _serialize_args(tool_args),
        "result": result_str,
        "status": "failed" if is_failed else "success",
        "error": error_str,
        "timestamp": datetime.now(tz=timezone(timedelta(hours=8))).isoformat(),
    }


def _serialize_args(args: Any) -> dict:
    """Normalize tool_args into a JSON-safe dict."""
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return {"value": args}
    return {"value": str(args)}


def _serialize_result(result: Any) -> str:
    """Serialize a tool result into a full (untruncated) string."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


def _format_results_section(
    entries: list[dict],
) -> str:
    """Build the markdown section to inject into the system prompt."""
    if not entries:
        return ""

    lines: list[str] = []
    lines.append("## 父 agent 最近工具调用结果")
    lines.append("")
    for i, entry in enumerate(entries, 1):
        lines.append(_format_entry(i, entry))
    lines.append("")

    return "\n".join(lines)


def _format_entry(index: int, entry: dict) -> str:
    """Format a single entry as: ``[i] tool  args={...}  ✓/✗  <result>``."""
    tool: str = entry.get("tool", "?")
    status: str = entry.get("status", "success")
    args: dict = entry.get("args", {})
    result: Optional[str] = entry.get("result")
    error: Optional[str] = entry.get("error")

    args_str = json.dumps(args, ensure_ascii=False, default=str)

    if status == "failed":
        head = f"[{index}] {tool}  args={args_str}  ✗  Error: {error}"
    else:
        head = f"[{index}] {tool}  args={args_str}  ✓"

    body = result if result else "(无结果)"
    return f"{head}\n    {body}"
