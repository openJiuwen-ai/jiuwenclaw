# coding: utf-8
"""Replaces SearchToolsTool: one-step semantic search + auto-load, no discovery/activation gap."""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

logger = logging.getLogger("jiuwenswarm.harness.common.tools.search_tool")

_DESCRIPTIONS = {
    "cn": (
        "搜索并加载工具。用自然语言描述你的需求，"
        "系统会找到最相关的工具并自动加载，加载后可直接调用。"
        "不需要再调用 load_tools。"
    ),
    "en": (
        "Search and load tools. Describe what you need in natural language, "
        "the system will find and auto-load the most relevant tools. "
        "No need to call load_tools separately."
    ),
}

# We manually construct ToolCard instead of using build_tool_card() because
# build_tool_card("search_tools", ...) resolves via the metadata provider
# registry to the OLD SearchToolsMetadataProvider's description.


class DenseSearchInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "Natural language description of what you want to do. "
            "用自然语言描述你想做什么。"
            "(e.g. 'search memory'/'搜索记忆', 'create scheduled task'/'创建定时任务', "
            "'query knowledge base'/'查知识库'). "
            "The system will find and auto-load the most relevant tools. "
            "系统会找到并自动加载最相关的工具。"
        ),
    )
    top_k: int = Field(
        default=3,
        description=(
            "Number of tools to return. 返回工具数量。"
            "Use default 3 when unsure; use 1 when you need exactly one tool. "
            "不确定时用默认值3；明确只需要1个工具时可用1。"
        ),
    )


class DenseSearchTool(Tool):
    """One-step tool discovery: semantic search + auto-load.

    TOOL_NAME='search_tools' is unchanged from v1 so the LLM doesn't
    notice the swap. The implementation now uses dense embedding retrieval.
    """

    TOOL_NAME = "search_tools"
    TOOL_ID = "DenseSearchTool"

    def __init__(
        self,
        search_fn: Callable[[str, int, int], Awaitable[List[Dict[str, Any]]]],
        load_fn: Callable[[Any, List[str]], Any],
        append_trace: Callable[[Any, Dict[str, Any]], None],
        language: str = "cn",
        agent_id: Optional[str] = None,
    ):
        self._language = language
        card = ToolCard(
            name=self.TOOL_NAME,
            description=_DESCRIPTIONS.get(language, _DESCRIPTIONS["cn"]),
            id=f"{self.TOOL_ID}_{agent_id}" if agent_id else self.TOOL_ID,
            input_params=DenseSearchInput.model_json_schema(),
        )
        super().__init__(card)
        self._search_fn = search_fn
        self._load_fn = load_fn
        self._append_trace = append_trace

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        try:
            parsed = DenseSearchInput(**(inputs or {}))
            top_k = max(1, min(parsed.top_k, 5))

            results = await self._search_fn(
                parsed.query, limit=top_k, detail_level=1
            )

            session = kwargs.get("session")
            evicted_names: List[str] = []
            loaded_names: List[str] = []
            load_ok = True
            if session is not None and results:
                loaded_names = [
                    r.get("name", "") for r in results if r.get("name")
                ]
                if loaded_names:
                    try:
                        _next, _added, evicted = self._load_fn(
                            session, loaded_names
                        )
                        evicted_names = evicted
                    except Exception as load_exc:
                        load_ok = False
                        logger.warning(
                            "[DenseSearchTool] auto-load failed (search OK): %s",
                            load_exc,
                        )

            self._append_trace(
                session,
                {
                    "action": "search_tools",
                    "query": parsed.query,
                    "top_k": top_k,
                    "match_count": len(results),
                    "auto_loaded": loaded_names if load_ok else [],
                    "evicted": evicted_names,
                    "load_error": not load_ok,
                },
            )

            logger.info(
                "[DenseSearchTool] query=%r | top_k=%d | found=%d | "
                "loaded=%s | evicted=%s | load_ok=%s",
                parsed.query,
                top_k,
                len(results),
                loaded_names[:3] if load_ok else [],
                evicted_names,
                load_ok,
            )

            is_cn = self._language != "en"
            if not results:
                note = (
                    "未找到相关工具，请尝试换一种描述。"
                    if is_cn
                    else "No matching tools found. Try a different description."
                )
            elif not load_ok:
                note = (
                    f"找到 {len(results)} 个工具，但自动加载失败。"
                    "可尝试手动调用 load_tools 加载。"
                    if is_cn
                    else f"Found {len(results)} tools, but auto-load failed. "
                    "Try calling load_tools manually."
                )
            else:
                note = (
                    "以上工具已自动加载，下一轮可直接调用。"
                    if is_cn
                    else "The above tools have been auto-loaded and are "
                    "callable in the next turn."
                )
                if evicted_names:
                    note += (
                        f"（为腾出空间，已移除：{', '.join(evicted_names)}）"
                        if is_cn
                        else f" (Evicted to make room: {', '.join(evicted_names)})"
                    )

            return ToolOutput(
                success=True,
                data={
                    "query": parsed.query,
                    "matches": results,
                    "count": len(results),
                    "note": note,
                },
            )
        except Exception as exc:
            logger.warning("[DenseSearchTool] invoke failed: %s", exc)
            return ToolOutput(success=False, error=str(exc))

    async def stream(
        self, inputs: Dict[str, Any], **kwargs
    ) -> AsyncIterator[Any]:
        """Satisfy async generator protocol; this tool does not stream."""
        if False:
            yield None
