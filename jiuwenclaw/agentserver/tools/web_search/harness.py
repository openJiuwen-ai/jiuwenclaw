# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.harness.prompts import resolve_language

from jiuwenclaw.agentserver.tools.web_search.tool import (
    build_web_search_tool_card,
    web_search,
)
from jiuwenclaw.utils import logger


class JiuwenHarnessWebSearchTool(Tool):
    """Unified web search for harness registration."""

    def __init__(
        self,
        language: str = "cn",
        agent_id: Optional[str] = None,
        card: Optional[ToolCard] = None,
    ) -> None:
        lang = resolve_language(language or "cn")
        super().__init__(
            card
            or build_web_search_tool_card(agent_id=agent_id, language=lang),
        )

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> Any:
        logger.debug(
            "[JiuwenHarnessWebTools] web_search card_id=%s",
            self.card.id,
        )
        return await web_search.invoke(inputs, **kwargs)

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)


__all__ = ["JiuwenHarnessWebSearchTool"]
