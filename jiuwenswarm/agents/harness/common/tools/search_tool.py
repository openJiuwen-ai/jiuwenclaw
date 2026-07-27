# coding: utf-8
"""Replaces SearchToolsTool: one-step semantic search returning full definitions."""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

logger = logging.getLogger("jiuwenswarm.harness.common.tools.search_tool")

_DESCRIPTIONS = {
    "cn": (
        "搜索工具。用自然语言描述你的需求，系统会找到最相关的工具"
        "并返回完整定义（含参数 JSON Schema）。返回的工具不在你的 tools 列表中，"
        "但已注册可直接按名称调用，无需加载。"
    ),
    "en": (
        "Search tools. Describe what you need in natural language; the system "
        "returns the most relevant tools with full definitions (including "
        "parameter JSON Schema). The returned tools are NOT in your tools "
        "list but are registered and directly callable by name — no loading "
        "required."
    ),
}

# We manually construct ToolCard instead of using build_tool_card() because
# build_tool_card("search_tools", ...) resolves via the metadata provider
# registry to the OLD SearchToolsMetadataProvider's description.


class _JsonToolOutput(ToolOutput):
    """ToolOutput whose str() is clean JSON for LLM consumption.

    ability_manager renders tool results to ToolMessage.content via
    str(result); pydantic's default __str__ yields a Python repr
    ('success=True data={...}') that is not valid JSON. Override so any
    provider parses the returned matches / JSON Schema / note without
    relying on model tolerance for single-quoted repr.
    """

    def __str__(self) -> str:
        payload = self.data if self.data is not None else {}
        return json.dumps(payload, ensure_ascii=False)


class DenseSearchInput(BaseModel):
    query: str = Field(
        ...,
        description=(
            "Natural language description of what you want to do. "
            "用自然语言描述你想做什么。"
            "(e.g. 'search memory'/'搜索记忆', 'create scheduled task'/'创建定时任务', "
            "'query knowledge base'/'查知识库'). "
            "The system will find and return the most relevant tools. "
            "系统会找到并返回最相关的工具。"
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
    """One-step tool discovery: semantic search returning full definitions.

    TOOL_NAME='search_tools' is unchanged from v1 so the LLM doesn't
    notice the swap. The implementation uses dense embedding retrieval and
    returns full tool definitions (including JSON Schema parameters) in the
    result. The LLM calls matched tools by name directly — they are resolved
    by ability_manager regardless of the request tools[] list, so the
    prefill stays cache-stable. Auto-load into session_visible is opt-in
    via load_fn (disabled by default).
    """

    TOOL_NAME = "search_tools"
    TOOL_ID = "DenseSearchTool"

    def __init__(
        self,
        search_fn: Callable[[str, int, int], Awaitable[List[Dict[str, Any]]]],
        append_trace: Callable[[Any, Dict[str, Any]], None],
        language: str = "cn",
        agent_id: Optional[str] = None,
        load_fn: Optional[Callable[[Any, List[str]], Any]] = None,
        top_k_max: int = 3,
    ):
        self._language = language
        self._top_k_max = top_k_max
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
            top_k = max(1, min(parsed.top_k, self._top_k_max))

            # detail_level=3 returns the full JSON Schema in "parameters" so the
            # LLM can construct valid arguments and call the tool by name directly,
            # without it being present in the request "tools" list.
            results = await self._search_fn(
                parsed.query, limit=top_k, detail_level=3
            )

            session = kwargs.get("session")
            loaded_names: List[str] = []
            evicted_names: List[str] = []
            load_ok = True
            # Auto-load is opt-in (load_fn provided). When disabled (the default),
            # matched tools stay out of session_visible so the request "tools"
            # list (prefill) stays constant for prompt-cache stability. The LLM
            # calls them by name directly — ability_manager resolves by name
            # regardless of the tools[] parameter.
            if self._load_fn is not None and session is not None and results:
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
                    f"找到 {len(results)} 个工具，但自动加载失败，请稍后重试搜索。"
                    if is_cn
                    else f"Found {len(results)} tools, but auto-load failed. "
                    "Please try searching again later."
                )
            elif self._load_fn is not None:
                # Auto-load path (legacy): tools are loaded into session_visible.
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
            else:
                # Default path: full definitions returned in the result; the LLM
                # calls tools by name directly. tools[] (prefill) is unchanged.
                note = (
                    "以上工具已找到（含完整参数定义）。这些工具不在你的 "
                    "tools 列表中，但已注册可直接按 name 调用。"
                    "请根据每个工具的 parameters（JSON Schema）构造参数，"
                    "直接发起 tool call。"
                    if is_cn
                    else "The tools above have been found with full "
                    "parameter definitions. They are NOT in your tools "
                    "list but are registered and directly callable by "
                    "name. Construct arguments from each tool's "
                    "'parameters' (JSON Schema) and call directly."
                )

            return _JsonToolOutput(
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

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        if False:
            yield None
