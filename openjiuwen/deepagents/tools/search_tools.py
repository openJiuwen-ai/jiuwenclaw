# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List

from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.deepagents.prompts.sections.tools import build_tool_card
from openjiuwen.deepagents.tools.base_tool import ToolOutput


class SearchToolsInput(BaseModel):
    query: str = Field(..., description="Search query for finding relevant candidate tools")
    limit: int = Field(default=10, description="Maximum number of candidate tools to return")
    detail_level: int = Field(
        default=1,
        description="1=name+description, 2=+parameter summary, 3=+full parameters",
    )


class SearchToolsTool(Tool):
    """Search candidate tools for progressive tool discovery."""

    TOOL_NAME = "search_tools"
    TOOL_ID = "SearchToolsTool"

    def __init__(
        self,
        search_tools: Callable[[str, int, int], Awaitable[List[Dict[str, Any]]]],
        append_trace: Callable[[Any, Dict[str, Any]], None],
        language: str = "cn",
    ):
        super().__init__(
            build_tool_card(self.TOOL_NAME, self.TOOL_ID, language)
        )
        self._search_tools = search_tools
        self._append_trace = append_trace

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        session = kwargs.get("session")
        try:
            parsed = SearchToolsInput(**(inputs or {}))
            limit = max(1, min(parsed.limit, 20))

            matches = await self._search_tools(
                parsed.query,
                limit,
                parsed.detail_level,
            )

            self._append_trace(
                session,
                {
                    "action": "search_tools",
                    "query": parsed.query,
                    "limit": limit,
                    "detail_level": parsed.detail_level,
                    "match_count": len(matches),
                },
            )

            logger.info(
                (
                    "[ProgressiveToolRail] search_tools invoked | query=%s | "
                    "limit=%s | detail_level=%s | match_count=%s | matched=%s"
                ),
                parsed.query,
                limit,
                parsed.detail_level,
                len(matches),
                [item.get("name", "") for item in matches],
            )

            return ToolOutput(
                success=True,
                data={
                    "query": parsed.query,
                    "matches": matches,
                    "count": len(matches),
                    "callability_note": (
                        "Search results are discovery-only. "
                        "Tools shown here are not callable until load_tools is called."
                    ),
                    "next_step_hint": (
                        "If the result is clear enough, call load_tools directly. "
                        "Increase detail_level to 2 or 3 when you need more parameter detail."
                    ),
                },
            )
        except Exception as exc:
            logger.warning(
                "[ProgressiveToolRail] search_tools invoke failed | error=%s",
                str(exc),
            )
            return ToolOutput(
                success=False,
                error=str(exc),
            )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        if False:
            yield None