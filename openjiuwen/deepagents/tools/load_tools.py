# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List

from pydantic import BaseModel, Field

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.deepagents.prompts.sections.tools import build_tool_card
from openjiuwen.deepagents.tools.base_tool import ToolOutput


class LoadToolsInput(BaseModel):
    tool_names: List[str] = Field(
        default_factory=list,
        description="Names of tools to make visible/callable for the current session",
    )
    replace: bool = Field(
        default=False,
        description="If true, replace the current visible tool set instead of merging",
    )


class LoadToolsTool(Tool):
    """Load selected real tools into the current session-visible tool set."""

    TOOL_NAME = "load_tools"
    TOOL_ID = "LoadToolsTool"

    def __init__(
        self,
        load_tools: Callable[[Any, List[str], bool], Awaitable[Dict[str, Any]]],
        language: str = "cn",
    ):
        super().__init__(
            build_tool_card(self.TOOL_NAME, self.TOOL_ID, language)
        )
        self._load_tools = load_tools

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        session = kwargs.get("session")
        try:
            parsed = LoadToolsInput(**(inputs or {}))
            result = await self._load_tools(
                session,
                parsed.tool_names,
                parsed.replace,
            )

            logger.info(
                "[ProgressiveToolRail] load_tools tool invoked | tool_names=%s | replace=%s | result=%s",
                list(parsed.tool_names),
                parsed.replace,
                result,
            )

            return ToolOutput(
                success=True,
                data=result,
            )
        except Exception as exc:
            logger.warning(
                "[ProgressiveToolRail] load_tools invoke failed | error=%s",
                str(exc),
            )
            return ToolOutput(
                success=False,
                error=str(exc),
            )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        if False:
            yield None