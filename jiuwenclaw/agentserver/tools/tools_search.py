# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""tools_search: Search deferred (on-demand) tools and return complete schema."""

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


class ToolsSearchInput(BaseModel):
    """Input schema for tools_search meta tool."""

    tool_name: str = Field(
        ...,
        description="按需可见工具的注册名称（与系统导航列表中的名称一致，精确匹配，忽略大小写）",
    )


class ToolsSearchTool(Tool):
    """Search deferred tools and return complete schema for invoke_tool."""

    TOOL_NAME = "tools_search"
    TOOL_ID = "ToolsSearchTool"

    def __init__(
        self,
        search_tools: Callable[
            [Any, ToolsSearchInput],
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
                    "按工具注册名查询按需可见工具的完整 input_schema。"
                    "入参 tool_name 须与导航列表中的名称一致（精确匹配）。"
                    "调用 invoke_tool 前必须先调用本工具获取 schema，再据此构造 arguments。"
                ),
                input_params=_model_schema(ToolsSearchInput),
            )
        )
        self._search_tools = search_tools

    async def invoke(self, inputs: dict[str, Any], **kwargs) -> dict[str, Any]:
        session = kwargs.get("session")
        try:
            parsed = ToolsSearchInput(**(inputs or {}))
            return await self._search_tools(session, parsed)
        except Exception as exc:
            logger.warning("[ToolsSearch] invoke failed: %s", exc)
            return {
                "success": False,
                "error": str(exc),
                "matches": [],
            }

    async def stream(self, inputs: dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        if False:
            yield None