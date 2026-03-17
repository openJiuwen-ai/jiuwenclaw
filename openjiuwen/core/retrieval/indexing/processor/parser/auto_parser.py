# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Auto Parser (top-level router)

Routes to AutoLinkParser (URLs) or AutoFileParser (local files). One entry point for "parse this path or URL".
Use one KB + AutoParser to accept both links (wechat, web) and files without separate APIs.
"""

from typing import List, Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.indexing.processor.parser.auto_file_parser import AutoFileParser
from openjiuwen.core.retrieval.indexing.processor.parser.auto_link_parser import (
    AutoLinkParser,
    HTTP_URL_PATTERN,
)
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser


def _is_likely_url(doc: str) -> bool:
    return bool(doc and HTTP_URL_PATTERN.match(doc.strip()))


class AutoParser(Parser):
    """
    Top-level router: URL → AutoLinkParser (WeChat / web by pattern), else → AutoFileParser (by extension).
    Single entry for parse(path_or_url); no need to choose parse_urls vs parse_files for mixed sources.
    """

    def __init__(
        self,
        link_parser: Parser | None = None,
        file_parser: Parser | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._link_parser = link_parser if link_parser is not None else AutoLinkParser()
        self._file_parser = file_parser if file_parser is not None else AutoFileParser()

    def supports(self, doc: str) -> bool:
        if _is_likely_url(doc):
            return self._link_parser.supports(doc)
        return self._file_parser.supports(doc)

    async def parse(self, doc: str, doc_id: str = "", llm_client: Optional[Model] = None, **kwargs) -> List[Document]:
        if _is_likely_url(doc) and self._link_parser.supports(doc):
            logger.debug("AutoParser delegating to link parser")
            return await self._link_parser.parse(doc, doc_id=doc_id, llm_client=llm_client, **kwargs)
        if self._file_parser.supports(doc):
            logger.debug("AutoParser delegating to file parser")
            return await self._file_parser.parse(doc, doc_id=doc_id, llm_client=llm_client, **kwargs)
        return []
