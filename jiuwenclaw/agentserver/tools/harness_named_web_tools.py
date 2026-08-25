# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.harness.prompts import resolve_language

from jiuwenclaw.agentserver.tools.web_fetch_tools import mcp_fetch_webpage_impl
from jiuwenclaw.agentserver.tools.web_search.harness import JiuwenHarnessWebSearchTool
from jiuwenclaw.utils import logger


def _override_fetch_card_url_array(base_card: ToolCard, lang: str) -> ToolCard:
    """Override the harness fetch_webpage card so ``url`` is an array.

    The upstream ``FetchWebpageMetadataProvider`` (openjiuwen package) still
    declares ``url`` as a string. We rebuild the card with an array schema and
    a multi-URL description so the LLM is told to pass several URLs at once.
    """
    ud = {
        "cn": "一个或多个要抓取的网页 URL。",
        "en": "One or more webpage URLs to fetch.",
    }[lang]
    description = {
        "cn": (
            "抓取网页文本，返回列表，每个元素含一个 URL 的状态码、标题和正文。"
            "可一次传入多个 URL 并行抓取。通常配合 web_search 使用：先搜索，"
            "再抓取前几个结果页。max_chars 支持 500-50000，"
            "timeout_seconds 支持 3-10 秒。"
        ),
        "en": (
            "Fetch webpage text and return a list; each item holds status, title and plain "
            "text of one URL. Accepts multiple URLs at once for parallel fetch. "
            "Usually used after web_search. max_chars accepts 500-50000 and "
            "timeout_seconds accepts 3-10 seconds."
        ),
    }[lang]
    input_params = {
        "type": "object",
        "properties": {
            "url": {"type": "array", "items": {"type": "string"}, "description": ud},
            "max_chars": {"type": "integer", "description": "每个 URL 返回内容最大字符数（500-50000）。", "default": 12000},
            "timeout_seconds": {"type": "integer", "description": "请求超时时间（秒，3-10）。", "default": 5},
        },
        "required": ["url"],
    }
    return ToolCard(
        id=base_card.id,
        name=base_card.name,
        description=description,
        input_params=input_params,
    )


class JiuwenHarnessFetchWebpageTool(Tool):
    """Fetch webpage; body from ``mcp_fetch_webpage``."""

    def __init__(
        self,
        language: str = "cn",
        agent_id: Optional[str] = None,
        card: Optional[ToolCard] = None,
        cache: Any | None = None,
    ) -> None:
        lang = resolve_language(language or "cn")
        from openjiuwen.harness.prompts.sections.tools import build_tool_card

        base_card = build_tool_card(
            "fetch_webpage",
            "JiuwenHarnessFetch",
            lang,
            agent_id=agent_id,
        )
        fetch_card = card or _override_fetch_card_url_array(base_card, lang)

        super().__init__(fetch_card)
        self._cache = cache

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> Any:
        logger.info(
            "[JiuwenHarnessWebTools] LLM tool name=fetch_webpage (card_id=%s)",
            self.card.id,
        )
        if self._cache is not None:
            kwargs["cache"] = self._cache
        return await mcp_fetch_webpage_impl(
            **inputs, **{k: v for k, v in kwargs.items() if k in ("cache",)}
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)


def build_jiuwen_harness_named_web_tools(
    *,
    agent_id: Optional[str],
    language: str = "cn",
    cache: Any | None = None,
) -> List[Tool]:
    """Build ``web_search`` + ``fetch_webpage`` for a scoped ``agent_id``."""
    lang = resolve_language(language or "cn")
    return [
        JiuwenHarnessWebSearchTool(language=lang, agent_id=agent_id, cache=cache),
        JiuwenHarnessFetchWebpageTool(language=lang, agent_id=agent_id, cache=cache),
    ]


__all__ = [
    "JiuwenHarnessFetchWebpageTool",
    "JiuwenHarnessWebSearchTool",
    "build_jiuwen_harness_named_web_tools",
]
