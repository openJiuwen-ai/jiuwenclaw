# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Auto Link / URL Parser

Routes by URL pattern (regex or callable). First matching route wins.
Default routes: WeChat article (mp.weixin.qq.com) → WeChatArticleParser, generic http(s) → WebPageParser.
Use one KB + AutoLinkParser so any URL (wechat or web) is auto-detected and parsed.
"""

import re
from typing import Any, Callable, List, Tuple, Union

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser

# Match http(s) URLs
HTTP_URL_PATTERN = re.compile(r"^https?://\S+", re.IGNORECASE)


def _default_routes() -> List[Tuple[Union[re.Pattern, Callable[[str], bool]], Parser]]:
    """Default (pattern_or_callable, parser): WeChat first, then generic web."""
    from openjiuwen.core.retrieval.indexing.processor.parser.wechat_article_parser import (
        WECHAT_MP_URL_PATTERN,
        WeChatArticleParser,
    )
    from openjiuwen.core.retrieval.indexing.processor.parser.web_page_parser import WebPageParser

    return [
        (WECHAT_MP_URL_PATTERN, WeChatArticleParser()),
        (HTTP_URL_PATTERN, WebPageParser()),
    ]


def _match_doc(pattern_or_callable: Union[re.Pattern, Callable[[str], bool]], doc: str) -> bool:
    if callable(pattern_or_callable):
        return bool(pattern_or_callable(doc))
    return bool(pattern_or_callable.match(doc.strip()))


class AutoLinkParser(Parser):
    """
    Parser that routes by URL pattern. Register (pattern, parser) pairs; first match wins.
    Default: WeChat article URLs (mp.weixin.qq.com) → WeChatArticleParser, other http(s) → WebPageParser.
    """

    def __init__(
        self,
        routes: List[Tuple[Union[re.Pattern, Callable[[str], bool]], Parser]] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.routes = routes if routes is not None else _default_routes()

    def supports(self, doc: str) -> bool:
        if not doc or not HTTP_URL_PATTERN.match(doc.strip()):
            return False
        return any(_match_doc(pat, doc) for pat, _ in self.routes)

    async def parse(self, doc: str, doc_id: str = "", llm_client: Any = None, **kwargs) -> List[Document]:
        for pattern_or_callable, parser in self.routes:
            if _match_doc(pattern_or_callable, doc):
                logger.debug("AutoLinkParser delegating to %s", type(parser).__name__)
                return await parser.parse(doc, doc_id=doc_id, **kwargs)
        return []
