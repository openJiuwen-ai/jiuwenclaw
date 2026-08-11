# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.harness.prompts import resolve_language

from jiuwenswarm.agents.harness.common.tools.web_fetch_tools import mcp_fetch_webpage
from jiuwenswarm.agents.harness.common.tools.web_search.harness import JiuwenHarnessWebSearchTool
from jiuwenswarm.common.utils import logger


def _build_fetch_webpage_tool_card(*, agent_id: Optional[str], language: str) -> ToolCard:
    """Build a harness ``fetch_webpage`` card from ``mcp_fetch_webpage`` metadata."""
    _ = language
    base = mcp_fetch_webpage.card
    card_id = "JiuwenHarnessFetch"
    if agent_id:
        card_id = f"{card_id}:{agent_id}"
    return ToolCard(
        id=card_id,
        name="fetch_webpage",
        description=base.description or "Fetch webpage text content.",
        input_params=dict(base.input_params or {}),
    )


class JiuwenHarnessFetchWebpageTool(Tool):
    """Fetch webpage; body from ``mcp_fetch_webpage``."""

    def __init__(
        self,
        language: str = "cn",
        agent_id: Optional[str] = None,
        card: Optional[ToolCard] = None,
    ) -> None:
        lang = resolve_language(language or "cn")
        super().__init__(
            card
            or _build_fetch_webpage_tool_card(agent_id=agent_id, language=lang)
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
