# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TokenJuiceRail — deterministic tool output compression.

Intercepts tool results via the ``after_tool_call`` hook and applies
rule-based compression before the output enters the LLM context window.

Port of the tokenjuice Node.js tool as a native Python Rail,
eliminating subprocess overhead and covering all tool entry points.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from openjiuwen.harness.rails.base import DeepAgentRail

logger = logging.getLogger(__name__)

# Safety thresholds (from the Rust reference implementation)
_MIN_OUTPUT_BYTES = 512          # Skip outputs smaller than this
_MAX_COMPRESSION_RATIO = 0.95   # Skip if compacted/raw > 0.95 (too little benefit)


class TokenJuiceRail(DeepAgentRail):
    """Compress tool outputs to reduce LLM context window usage.

    Hooks into ``after_tool_call`` to intercept tool results, applies
    tokenjuice rule-based compression, and replaces the result if
    compression yields meaningful savings.
    """

    priority = 15  # After TelemetryRail(10), before business rails

    def __init__(self, max_inline_chars: int = 1200) -> None:
        super().__init__()
        self._rules = None  # Lazy-loaded on first use
        self._max_inline_chars = max_inline_chars

    @staticmethod
    def _extract_command(tool_name: str, tool_args: Any) -> tuple[str | None, list[str] | None]:
        """Extract command string and argv from tool arguments."""
        if tool_args is None:
            return None, None

        # tool_args may be a dict or a JSON string
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except (json.JSONDecodeError, ValueError):
                return tool_args, None

        if not isinstance(tool_args, dict):
            return None, None

        # Common patterns
        command = tool_args.get("command") or tool_args.get("cmd")
        if command:
            return str(command), None

        # MCP exec_command style
        if "args" in tool_args and isinstance(tool_args["args"], list):
            return None, [str(a) for a in tool_args["args"]]

        return None, None
    
    def init(self, agent: Any) -> None:
        """Pre-load and compile rules when the rail is registered."""
        try:
            from jiuwenswarm.common.tokenjuice import load_rules
            self._rules = load_rules()
            logger.info("[TokenJuiceRail] initialized with %d rules", len(self._rules))
        except Exception as exc:
            logger.warning("[TokenJuiceRail] failed to load rules: %s", exc)
            self._rules = None

    def uninit(self, agent: Any) -> None:
        self._rules = None
        logger.info("[TokenJuiceRail] uninitialized")

    async def after_tool_call(self, ctx: Any) -> None:
        """Compress tool result before it enters the LLM context."""
        if self._rules is None:
            return

        # Extract tool info
        tool_name = getattr(ctx.inputs, "tool_name", "") or ""
        tool_args = getattr(ctx.inputs, "tool_args", None)
        tool_result = getattr(ctx.inputs, "tool_result", None)

        if tool_result is None:
            return

        # Convert result to string
        result_text = str(tool_result) if not isinstance(tool_result, str) else tool_result

        # Safety: skip tiny outputs
        if len(result_text.encode("utf-8")) < _MIN_OUTPUT_BYTES:
            return

        # Extract command from tool args
        command, argv = self._extract_command(tool_name, tool_args)

        # Build input
        from jiuwenswarm.common.tokenjuice import reduce_execution, ToolExecutionInput, ReduceOptions

        input_ = ToolExecutionInput(
            tool_name="exec",
            command=command,
            argv=argv,
            stdout=result_text,
            exit_code=0,
        )

        opts = ReduceOptions(max_inline_chars=self._max_inline_chars)

        # Run compression
        try:
            result = reduce_execution(input_, self._rules, opts)
        except Exception as exc:
            logger.warning("[TokenJuiceRail] compression error: %s", exc)
            return

        # Check if compression is worthwhile
        stats = result.stats or {}
        ratio = stats.get("ratio", 1.0)
        raw_chars = stats.get("raw_chars", 0)
        reduced_chars = stats.get("reduced_chars", 0)

        if reduced_chars >= raw_chars:
            return  # No savings

        if ratio > _MAX_COMPRESSION_RATIO:
            return  # Too little benefit

        # Replace the tool result
        compacted = result.inline_text
        ctx.inputs.tool_result = compacted

        # Also update tool_msg if present
        tool_msg = getattr(ctx.inputs, "tool_msg", None)
        if tool_msg and hasattr(tool_msg, "content"):
            tool_msg.content = compacted

        logger.info(
            "[TokenJuiceRail] compacted %s | rule=%s | raw=%d → reduced=%d (%.1f%% saved)",
            tool_name,
            result.classification.matched_reducer if result.classification else "?",
            raw_chars,
            reduced_chars,
            (1.0 - ratio) * 100,
        )
