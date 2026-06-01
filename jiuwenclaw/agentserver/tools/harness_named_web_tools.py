# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.harness.prompts import resolve_language

from jiuwenclaw.agentserver.tools.web_fetch_tools import mcp_fetch_webpage
from jiuwenclaw.agentserver.tools.web_search.harness import JiuwenHarnessWebSearchTool
from jiuwenclaw.utils import logger


class JiuwenHarnessFetchWebpageTool(Tool):
    """Fetch webpage; body from ``mcp_fetch_webpage``."""

    def __init__(
        self,
        language: str = "cn",
        agent_id: Optional[str] = None,
        card: Optional[ToolCard] = None,
    ) -> None:
        lang = resolve_language(language or "cn")
        from openjiuwen.harness.prompts.sections.tools import build_tool_card

        super().__init__(
            card
            or build_tool_card(
                "fetch_webpage",
                "JiuwenHarnessFetch",
                lang,
                agent_id=agent_id,
            )
        )

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> Any:
        logger.info(
            "[JiuwenHarnessWebTools] LLM tool name=fetch_webpage (card_id=%s)",
            self.card.id,
        )
        return await mcp_fetch_webpage.invoke(inputs, **kwargs)

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)


def build_jiuwen_harness_named_web_tools(
    *,
    agent_id: Optional[str],
    language: str = "cn",
) -> List[Tool]:
    """Build ``web_search`` + ``fetch_webpage`` for a scoped ``agent_id``."""
    lang = resolve_language(language or "cn")
    return [
        JiuwenHarnessWebSearchTool(language=lang, agent_id=agent_id),
        JiuwenHarnessFetchWebpageTool(language=lang, agent_id=agent_id),
    ]


__all__ = [
    "JiuwenHarnessFetchWebpageTool",
    "JiuwenHarnessWebSearchTool",
    "build_jiuwen_harness_named_web_tools",
]
