"""SearchAgent dispatch tool — wraps the custom ReAct search loop.

Mirrors :class:`AgentTool` (``code_agent_rail.py``): a :class:`Tool` the parent
LLM calls to delegate a search task. Unlike ``AgentTool`` (which dispatches a
DeepAgent sub-agent), this tool runs the vendored ``MMSearchAgent.react_loop``
— a lighter ReAct loop with its own context manager, web tools, and a
**dedicated search model** carried on ``AgentConfig`` (independent of the
parent agent's model).

The "search -> fetch -> summarize" behaviour emerges from the inner ReAct
loop (the search model decides when to call ``web_search`` /
``web_fetch_and_summary`` and produces the final answer), so no separate
fetch-and-summarize tool is needed.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

from jiuwenswarm.agents.harness.search.agent.nlp_react_agent import (
    AgentConfig,
    MMSearchAgent,
    MMSearchInput,
)
from jiuwenswarm.agents.harness.search.agent.trace import ReactLoopResult

_AGENT_ID = "search_agent"

_SEARCH_AGENT_TOOL_DESCRIPTION = (
    "Delegate a search/research task to an isolated SearchAgent with its own "
    "dedicated search model and ReAct loop (web_search + web_fetch_and_summary). "
    "Provide a single, self-contained search question as `prompt`. "
    "The agent runs autonomously (search -> fetch -> summarize) and returns only "
    "the final answer; intermediate tool calls stay inside the subagent's context "
    "and do not pollute the parent conversation."
)


def build_search_agent_tool_card(agent_id: str | None = None) -> ToolCard:
    """Build the ``ToolCard`` advertising the SearchAgent dispatch tool."""
    tool_id = f"search_agent_run_{agent_id}" if agent_id else f"search_agent_run_{uuid.uuid4().hex}"
    return ToolCard(
        id=tool_id,
        name="search_agent_run",
        description=_SEARCH_AGENT_TOOL_DESCRIPTION,
        input_params={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The search/research question to delegate to the SearchAgent.",
                },
                "description": {
                    "type": "string",
                    "description": "A short (3-5 word) label for the task.",
                },
            },
            "required": ["prompt"],
        },
        properties={"resilience": {"timeout_s": None}},  # 豁免，靠内层 max_iterations 自管，兜底 3600s
        # 复用单个 MMSearchAgent（共享 ContextManager 的可变 token 统计），
        # 故不可与同一轮其他 search_agent_run 并发；由 harness 串行调度。
        parallel_safe=False,
    )


def _extract_final_answer(result: ReactLoopResult) -> str:
    """Pull the final answer out of a ``ReactLoopResult``.

    The loop returns when ``finish_reason != "tool_calls"``; the final LLM text
    is recorded as the last ``TraceItem(type="output_text")``. Fall back to the
    failure/other-info strings when no output_text trace was captured.
    """
    for item in reversed(result.trace_list):
        if item.type == "output_text" and item.output:
            return item.output
    if result.failure_reason:
        return result.failure_reason
    if result.other_info:
        return result.other_info
    return ""


class SearchAgentTool(Tool):
    """Dispatch tool that runs the vendored ``MMSearchAgent.react_loop``.

    A single ``MMSearchAgent`` is constructed lazily on first invoke and
    **reused** across invokes — amortizes client/registry construction and,
    more importantly, keeps the LLM + web httpx connection pools warm (no
    per-invoke teardown/reconnect). ``close()`` is NOT called per invoke; see
    :meth:`close` for shutdown cleanup.

    ``parallel_safe=False`` because the reused agent shares a ContextManager
    (mutable token stats); the harness therefore serializes concurrent
    ``search_agent_run`` dispatches in the same turn.
    """

    def __init__(self, card: ToolCard, agent_config: AgentConfig, logger: logging.Logger) -> None:
        super().__init__(card)
        self._agent_config = agent_config
        self._logger = logger
        self._agent: MMSearchAgent | None = None
        self._init_lock = asyncio.Lock()

    async def _get_agent(self) -> MMSearchAgent:
        # 懒构造：tool-build 时不付 client/web-tool 初始化成本（asyncio.Semaphore
        # 等需事件循环），首次 invoke 时才建，之后跨 invoke 复用。
        if self._agent is None:
            async with self._init_lock:
                if self._agent is None:
                    self._agent = MMSearchAgent(self._agent_config, logger=self._logger)
        return self._agent

    async def invoke(self, inputs: Any, **kwargs: Any) -> ToolOutput:
        prompt = _get_input(inputs, "prompt") or _get_input(inputs, "query") or ""
        if not prompt:
            return ToolOutput(
                success=False,
                data={"output": "SearchAgent requires a non-empty `prompt`.", "agent_id": _AGENT_ID},
            )

        try:
            agent = await self._get_agent()
            result: ReactLoopResult = await agent.react_loop(MMSearchInput(question=prompt))
            answer = _extract_final_answer(result) or "(no answer)"
            return ToolOutput(
                success=result.success,
                data={
                    "output": answer,
                    "agent_id": _AGENT_ID,
                    "usage": result.total_usage,
                    "finish_reason": result.finish_reason,
                    "failure_reason": result.failure_reason,
                },
            )
        except Exception as exc:  # noqa: BLE001 - surface failure to the parent LLM, don't crash the turn
            self._logger.error("[SearchAgentTool] react_loop failed: %s", exc, exc_info=True)
            return ToolOutput(
                success=False,
                data={"output": f"SearchAgent failed: {exc}", "agent_id": _AGENT_ID},
            )
        # NOTE: 不在此 close() —— agent 跨 invoke 复用，保留 LLM/web httpx 连接池。
        # 进程退出时由 SearchAgentTool.close() 统一释放。

    async def close(self) -> None:
        """释放复用 agent 的 httpx 连接池，退出时调用。"""
        if self._agent is not None:
            try:
                await self._agent.close()
            except Exception:  # noqa: BLE001
                self._logger.debug("[SearchAgentTool] agent.close() raised, ignoring", exc_info=True)
            self._agent = None

    async def stream(self, inputs: Any, **kwargs: Any) -> None:
        # The custom loop is not a streaming-DeepAgent; the parent harness drives
        # it via invoke(). Match AgentTool's no-stream contract.
        return None


def _get_input(inputs: Any, key: str) -> str:
    """Read a string field from a dict or attribute-style inputs object."""
    if isinstance(inputs, dict):
        val = inputs.get(key)
    else:
        val = getattr(inputs, key, None)
    return val if isinstance(val, str) else ""


__all__ = ["SearchAgentTool", "build_search_agent_tool_card"]
