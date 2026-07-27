"""Agent-facing toolkit for progressive tool retrieval.

Mirrors ``SkillRetrievalToolkit``, but built on the Tool tree index
instead of the Skill tree index.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenswarm.symphony.tool_index.api import (
    build_tool_index as build_tool_index_blocking,
)
from jiuwenswarm.symphony.agent.agentic_retrieval_toolkit import (
    AgenticRetrievalToolKit,
)

# ---------------------------------------------------------------------------
# Label rewriter — transforms skill-oriented prompt / output text produced by
# the shared AgenticRetrievalToolKit into tool-oriented labels without
# touching the shared code.
# ---------------------------------------------------------------------------

def _rerender_tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply ``_rerender_for_tools`` to the result text and skill_tree metadata."""
    if isinstance(payload.get("result"), str):
        payload["result"] = _rerender_for_tools(payload["result"])
    # AgenticToolResult stores the full output (with skill_tree) in a
    # separate ``detailed_output`` attribute — transform that too so the
    # frontend sees ``tool_branch_explore`` in the query field.
    detailed = getattr(payload, "detailed_output", None)
    if isinstance(detailed, dict):
        if isinstance(detailed.get("result"), str):
            detailed["result"] = _rerender_for_tools(detailed["result"])
        st = detailed.get("skill_tree")
        if isinstance(st, dict) and isinstance(st.get("query"), str):
            st["query"] = _rerender_for_tools(st["query"])
    return payload


def _rerender_for_tools(text: str) -> str:
    """Rewrite skill-flavoured labels in *text* for the tool-retrieval UX."""
    replacements = [
        # English (longer patterns first)
        ("Agentic Skill Retrieval", "Agentic Tool Retrieval"),
        ("Skill Retrieval", "Tool Retrieval"),
        ("# Skill Branch Explore", "# Tool Branch Explore"),
        ("# Skill Branch Peek", "# Tool Branch Peek"),
        ("### skills", "### tools"),
        ("skill_branch_explore", "tool_branch_explore"),
        ("skill_branch_peek", "tool_branch_peek"),
        ("skill_index_build", "tool_index_build"),
        ("installed skills", "registered tools"),
        ("skill-directory", "tool-directory"),
        ("skill entries", "tool entries"),
        ("a `skills` section", "a tools section"),
        ("`skills` section", "tools section"),
        # Chinese
        ("Agentic 技能检索", "Agentic 工具检索"),
        ("技能检索", "工具检索"),
        ("已安装技能", "已注册工具"),
        ("技能目录", "工具目录"),
        ("技能选择", "工具选择"),
        ("叶子技能", "叶子工具"),
        ("精确技能", "精确工具"),
        ("宽泛概览技能", "宽泛概览工具"),
        # Catch-all
        ("Skill", "Tool"),
        ("SKILL.md", "tool definition"),
        ("skill", "tool"),
        ("技能", "工具"),
    ]
    result = text
    for old, new in replacements:
        result = result.replace(old, new)
    return result
from jiuwenswarm.symphony.tool_index.config import (
    ToolIndexConfig,
    load_tool_index_config,
)
from jiuwenswarm.symphony.tool_index.scanner import ToolScanner

LOGGER = logging.getLogger(__name__)

# Module-level cache set by interface_deep._get_tool_cards() after all tools
# are collected.  Avoids the complexity of walking the agent call stack or
# navigating Runner.resource_mgr internals at tool-invocation time.
_runtime_tool_cards: dict[str, Any] | None = None


def set_runtime_tool_cards(cards: dict[str, Any] | None) -> None:
    """Called by the agent adapter after building the full tool_cards list."""
    global _runtime_tool_cards
    _runtime_tool_cards = dict(cards) if cards else None


def _resolve_tool_cards() -> dict[str, Any] | None:
    """Return the cached tool_cards set by the agent adapter."""
    global _runtime_tool_cards
    if _runtime_tool_cards:
        LOGGER.info("_resolve_tool_cards: returning %d cached tools", len(_runtime_tool_cards))
        return dict(_runtime_tool_cards)
    LOGGER.warning("_resolve_tool_cards: cache is empty!")
    return None


class ToolRetrievalToolkit:
    """Expose progressive installed-tool tree retrieval to agents."""

    def __init__(
        self,
        *,
        tool_cards: dict[str, Any] | None = None,
        config: ToolIndexConfig | None = None,
    ) -> None:
        self._tool_cards = tool_cards
        self._config = config or load_tool_index_config()

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def tool_index_build(self) -> dict[str, Any]:
        """Build or refresh the local tool retrieval index."""
        cards = self._tool_cards or _resolve_tool_cards()
        if cards is None:
            return {
                "success": False,
                "result": (
                    "# Tool Index Build\n\n"
                    "Tool registry is not available. "
                    "Continue with the original jiuwenswarm flow."
                ),
            }
        try:
            payload = await asyncio.to_thread(
                build_tool_index_blocking,
                cards,
                config=self._config,
                force=False,
            )
        except Exception as exc:
            LOGGER.exception("tool_index_build failed")
            return {"success": False, "result": f"# Tool Index Build\n\nBuild failed: {exc}"}
        return {
            "success": bool(payload.get("success")),
            "result": str(payload.get("result") or ""),
        }

    async def tool_branch_explore(self, node_ids: list[str]) -> dict[str, Any]:
        """Explore tool-tree branch nodes."""
        return await self._run_explore(node_ids, peek=False)

    async def tool_branch_peek(self, node_ids: list[str]) -> dict[str, Any]:
        """Lightweight preview of tool-tree branch summaries."""
        return await self._run_explore(node_ids, peek=True)

    async def _run_explore(
        self, node_ids: list[str], *, peek: bool
    ) -> dict[str, Any]:
        cards = self._tool_cards or _resolve_tool_cards()
        if cards is None:
            return {
                "success": False,
                "result": "# Tool Tree\n\nTool registry is not available.",
            }

        # Quick status check
        from jiuwenswarm.symphony.tool_index.api import tool_index_status

        status = tool_index_status(cards, config=self._config)
        if not status.get("index_exists"):
            return {
                "success": False,
                "result": (
                    "# Tool Tree Retrieval Unavailable\n\n"
                    "Tool retrieval index does not exist.\n\n"
                    "Call `tool_index_build` before using "
                    "`tool_branch_explore` or `tool_branch_peek`."
                ),
            }
        if not status.get("fresh"):
            return {
                "success": False,
                "result": (
                    "# Tool Tree Retrieval Unavailable\n\n"
                    "Tool retrieval index is stale.\n\n"
                    "Call `tool_index_build`, then call "
                    "`tool_branch_explore` with known branch node ids."
                ),
            }

        index_dir = self._config.artifact_root / "index"
        try:
            toolkit = AgenticRetrievalToolKit.from_index(index_dir)
        except Exception as exc:
            LOGGER.exception("Failed to load tool index")
            return {
                "success": False,
                "result": f"# Tool Tree\n\nFailed to load tool index: {exc}",
            }

        if peek:
            raw = toolkit.skill_branch_peek(node_ids)
        else:
            raw = toolkit.skill_branch_explore(node_ids)
        return _rerender_tool_result(raw)

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def get_tools(self) -> list[Tool]:
        """Return progressive-tool-retrieval tools for agent registration."""

        def _tool(
            name: str,
            description: str,
            input_params: dict[str, Any],
            func: Callable[..., Any],
        ) -> Tool:
            card = ToolCard(
                id=name,
                name=name,
                description=description,
                input_params=input_params,
            )
            return LocalFunction(card=card, func=func)

        return [
            _tool(
                "tool_index_build",
                (
                    "Build or refresh the local tree index for registered tools. "
                    "Do not call this proactively. First call tool_branch_explore "
                    "or tool_branch_peek; call tool_index_build only if those "
                    "tools return a result that explicitly says the index is "
                    "missing or stale and instructs you to build it."
                ),
                {"type": "object", "properties": {}, "required": []},
                self.tool_index_build,
            ),
            _tool(
                "tool_branch_explore",
                (
                    "Primary tool-directory browsing tool for registered tools. "
                    "Explore one or more branch node ids to disclose the next "
                    "visible tool-tree boundary. Use this directly with "
                    "first-level category ids already shown in the system prompt, "
                    "or with branch ids returned by previous tool results. "
                    "Do not call this with ROOT; ROOT is already summarized in "
                    "the system prompt. "
                    "When the result contains a 'skills' section, those entries "
                    "are registered tools, not branch ids; use Name and "
                    "Description to shortlist and call the tool directly. "
                    "If the index is missing or stale, follow the returned "
                    "instruction to call tool_index_build once, then retry."
                ),
                {
                    "type": "object",
                    "properties": {
                        "node_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Branch node ids to explore. Use first-level "
                                "category ids from the system prompt or branch "
                                "ids returned by previous retrieval results. "
                                "Do not use ['ROOT']."
                            ),
                        },
                    },
                    "required": ["node_ids"],
                },
                self.tool_branch_explore,
            ),
            _tool(
                "tool_branch_peek",
                (
                    "Lightweight tool-directory preview tool for registered tools. "
                    "Use this only when you are unsure whether a branch is worth "
                    "exploring. It returns child branch summaries and coverage "
                    "information; it does not return full leaf tool details. "
                    "Use tool_branch_explore to disclose actual tool entries "
                    "when a branch looks relevant. "
                    "If the index is missing or stale, follow the returned "
                    "instruction to call tool_index_build once, then retry."
                ),
                {
                    "type": "object",
                    "properties": {
                        "node_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Branch node ids to preview. Use ['ROOT'] only "
                                "when you need to rediscover top-level branch "
                                "summaries."
                            ),
                        },
                    },
                    "required": ["node_ids"],
                },
                self.tool_branch_peek,
            ),
        ]
