# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""search_and_load_tools: one-step progressive tool discovery and exposure."""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Awaitable, Callable

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import Tool, ToolCard

logger = logging.getLogger(__name__)


def _model_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema_fn = getattr(model, "model_json_schema", None)
    if callable(schema_fn):
        return schema_fn()
    return model.schema()


class SearchAndLoadToolsInput(BaseModel):
    """Input schema for the progressive search-and-load meta tool."""

    query: str = Field(..., description="Search query for finding relevant registered tools")
    source: str = Field(default="all", description="Optional source/category filter. Use 'all' by default.")
    limit: int = Field(default=3, description="Maximum tools to load from search results")
    detail_level: int = Field(
        default=1,
        description="1=name+description, 2=+parameter summary, 3=+full parameters",
    )
    replace: bool = Field(
        default=False,
        description="If true, replace current session-visible tools instead of merging",
    )


class SearchAndLoadToolsTool(Tool):
    """Search registered tools and make the best matches visible in the current session."""

    TOOL_NAME = "search_and_load_tools"
    TOOL_ID = "SearchAndLoadToolsTool"

    def __init__(
        self,
        search_and_load_tools: Callable[
            [Any, SearchAndLoadToolsInput],
            Awaitable[dict[str, Any]],
        ],
        *,
        language: str = "cn",
        agent_id: str | None = None,
    ) -> None:
        _ = language
        tool_id = f"{self.TOOL_ID}_{agent_id}" if agent_id else self.TOOL_ID
        super().__init__(
            ToolCard(
                id=tool_id,
                name=self.TOOL_NAME,
                description=(
                    "搜索已注册但当前不可见的工具，并把最相关的候选工具加载到当前 session 的可调用工具集。"
                    "当你需要使用当前 tools 列表里没有出现的能力时，先调用本工具；"
                    "本工具会返回已加载工具名，下一轮模型调用即可直接调用这些真实工具。"
                ),
                input_params=_model_schema(SearchAndLoadToolsInput),
            )
        )
        self._search_and_load_tools = search_and_load_tools

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> dict[str, Any]:
        session = kwargs.get("session")
        try:
            parsed = SearchAndLoadToolsInput(**(inputs or {}))
            limit = max(1, min(int(parsed.limit or 3), 20))
            detail_level = max(1, min(int(parsed.detail_level or 1), 3))
            return await self._search_and_load_tools(
                session,
                parsed.model_copy(
                    update={"limit": limit, "detail_level": detail_level},
                ),
            )
        except Exception as exc:
            logger.warning("[ProgressiveTool] search_and_load_tools invoke failed: %s", exc)
            return {
                "success": False,
                "error": str(exc),
                "loaded_tools": [],
                "visible_tools": [],
                "skipped_tools": [],
            }

    async def stream(self, inputs: dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        if False:
            yield None
