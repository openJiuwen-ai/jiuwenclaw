# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Generic Web Page Parser

Fetches and parses generic web page URLs (e.g. blog posts, articles) into Document objects.
Uses BeautifulSoup; main content is extracted via common selectors (article, main, etc.).
For WeChat articles use WeChatArticleParser instead.
"""

import re
import ssl
import uuid
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.indexing.processor.parser.wechat_article_parser import (
    _is_wechat_article_url,
)

# http(s) URL pattern
HTTP_URL_PATTERN = re.compile(r"^https?://\S+", re.IGNORECASE)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 30.0

# Selectors to try for main content (in order)
MAIN_CONTENT_SELECTORS = [
    "article",
    "main",
    '[role="main"]',
    ".article-body",
    ".post-content",
    ".content",
    ".entry-content",
    ".post-body",
    "#content",
    ".main-content",
]


def _parse_html(html: str) -> BeautifulSoup:
    try:
        import lxml  # noqa: F401
        return BeautifulSoup(html, "lxml")
    except ImportError:
        return BeautifulSoup(html, "html.parser")


def _extract_title(soup: BeautifulSoup) -> str:
    meta = soup.find("meta", property="og:title")
    if meta and meta.get("content"):
        return (meta["content"] or "").strip()
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        return title_tag.string.strip()
    return ""


def _find_main_content(soup: BeautifulSoup) -> Optional[BeautifulSoup]:
    """Find main content node by trying common selectors."""
    for sel in MAIN_CONTENT_SELECTORS:
        node = soup.select_one(sel)
        if node and _text_length(node) > 100:
            return node
    # Fallback: largest block with substantial text
    body = soup.find("body")
    if body:
        for tag in body.find_all(["article", "main", "div", "section"]):
            if _text_length(tag) > 200:
                return tag
    return body


def _text_length(node: BeautifulSoup) -> int:
    """Approximate main-text length (exclude script/style) without mutating the tree."""
    total = 0
    for elem in node.find_all(string=True):
        if elem.parent and elem.parent.name not in ("script", "style"):
            total += len(elem.strip())
    return total


def _get_text_from_soup(soup: Optional[BeautifulSoup]) -> str:
    if not soup:
        return ""
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


async def parse_web_page_url(
    url: str,
    doc_id: str = "",
    *,
    timeout: float = DEFAULT_TIMEOUT,
    user_agent: str = DEFAULT_USER_AGENT,
    verify: bool | str | ssl.SSLContext = True,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Document]:
    """
    Fetch a web page URL and parse it into one or more Document objects.

    Args:
        url: Web page URL (http or https).
        doc_id: Optional document ID; defaults to URL or generated UUID.
        timeout: Request timeout in seconds (used only when ``client`` is not provided).
        user_agent: HTTP User-Agent header.
        verify: SSL verification for the httpx client: ``True`` (default CA bundle),
            ``False`` to disable (e.g. corporate TLS inspection), a path to a CA bundle, or a custom
            ``ssl.SSLContext`` — same semantics as :class:`httpx.AsyncClient`.
        client: Optional shared :class:`httpx.AsyncClient`; if omitted, a client is created with
            ``verify``, ``timeout``, and ``User-Agent`` and closed after the request.

    Returns:
        List of Document instances (typically one).

    Raises:
        build_error(StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR): On invalid URL or fetch/parse failure.
    """
    url = (url or "").strip()
    if not url or not HTTP_URL_PATTERN.match(url):
        raise build_error(
            StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
            error_msg=f"Not a valid HTTP URL: {url!r}",
        )
    if _is_wechat_article_url(url):
        raise build_error(
            StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
            error_msg="Use WeChatArticleParser for WeChat URLs",
        )

    request_headers = {"User-Agent": user_agent}

    async def _do_get(c: httpx.AsyncClient, *, headers_for_request: Optional[dict] = None) -> str:
        response = await c.get(url, headers=headers_for_request)
        response.raise_for_status()
        return response.text

    try:
        if client is not None:
            html = await _do_get(client, headers_for_request=request_headers)
        else:
            async with httpx.AsyncClient(
                verify=verify,
                timeout=httpx.Timeout(timeout),
                headers=request_headers,
            ) as http_client:
                html = await _do_get(http_client, headers_for_request=None)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else "?"
        raise build_error(
            StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
            error_msg=f"Web page request failed: {status} for {url}",
            cause=e,
        ) from e
    except httpx.RequestError as e:
        raise build_error(
            StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
            error_msg=f"Web page fetch failed for {url}: {e}",
            cause=e,
        ) from e
    except Exception as e:
        raise build_error(
            StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
            error_msg=f"Web page fetch failed for {url}: {e}",
            cause=e,
        ) from e

    soup = _parse_html(html)
    title = _extract_title(soup)
    content_node = _find_main_content(soup)
    if not content_node:
        raise build_error(
            StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
            error_msg=f"Could not find main content in page: {url}",
        )
    text = _get_text_from_soup(content_node)
    if not text or len(text) < 50:
        raise build_error(
            StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
            error_msg=f"Article content too short or empty after parsing: {url}",
        )

    effective_id = doc_id or url or str(uuid.uuid4())
    doc = Document(
        id_=effective_id,
        text=text,
        metadata={
            "source_url": url,
            "title": title or "(无标题)",
            "source_type": "web_page",
        },
    )
    logger.info("Parsed web page: url=%s title=%s", url, title or "(无标题)")
    return [doc]


class WebPageParser(Parser):
    """
    Parser for generic web page URLs (blogs, articles, etc.).
    Use AutoParser or AutoLinkParser for auto-dispatch; WeChat URLs are routed to WeChatArticleParser by pattern.
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
        verify: bool | str | ssl.SSLContext = True,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.timeout = timeout
        self.user_agent = user_agent
        self.verify = verify

    async def parse(self, doc: str, doc_id: str = "", **kwargs) -> List[Document]:
        timeout = kwargs.get("timeout", self.timeout)
        user_agent = kwargs.get("user_agent", self.user_agent)
        verify = kwargs.get("verify", self.verify)
        client = kwargs.get("client")
        return await parse_web_page_url(
            doc,
            doc_id=doc_id or doc,
            timeout=timeout,
            user_agent=user_agent,
            verify=verify,
            client=client,
        )

    def supports(self, doc: str) -> bool:
        """True for http(s) URLs that are not WeChat article URLs."""
        if not doc or not HTTP_URL_PATTERN.match(doc.strip()):
            return False
        return not _is_wechat_article_url(doc)
