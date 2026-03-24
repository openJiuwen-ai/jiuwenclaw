# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
WeChat Official Account Article Parser

Fetches and parses WeChat article pages from URLs (e.g. https://mp.weixin.qq.com/s/...)
into Document objects for indexing. Uses BeautifulSoup for HTML parsing.
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

# Recognized WeChat article URL pattern
WECHAT_MP_URL_PATTERN = re.compile(
    r"^https?://(?:mp\.weixin\.qq\.com|.*?\.weixin\.qq\.com)/s\b.*",
    re.IGNORECASE,
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 30.0


def _is_wechat_article_url(url: str) -> bool:
    return bool(url and WECHAT_MP_URL_PATTERN.match(url.strip()))


def _parse_html(html: str) -> BeautifulSoup:
    """Parse HTML with BeautifulSoup (lxml if available, else html.parser)."""
    try:
        import lxml  # noqa: F401
        return BeautifulSoup(html, "lxml")
    except ImportError:
        return BeautifulSoup(html, "html.parser")


def _extract_title(soup: BeautifulSoup) -> str:
    """Extract article title from parsed HTML (og:title or <title>)."""
    meta = soup.find("meta", property="og:title")
    if meta and meta.get("content"):
        return (meta["content"] or "").strip()
    title_tag = soup.find("title")
    if title_tag and title_tag.string:
        return title_tag.string.strip()
    return ""


def _extract_js_content(soup: BeautifulSoup) -> Optional[BeautifulSoup]:
    """Get the div with id='js_content' (article body)."""
    div = soup.find("div", id="js_content")
    return div


def _get_text_from_soup(soup: Optional[BeautifulSoup]) -> str:
    """Get plain text from a BeautifulSoup node, strip tags and normalize whitespace."""
    if not soup:
        return ""
    # Remove script/style
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    # Normalize whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


async def parse_wechat_article_url(
    url: str,
    doc_id: str = "",
    *,
    timeout: float = DEFAULT_TIMEOUT,
    user_agent: str = DEFAULT_USER_AGENT,
    verify: bool | str | ssl.SSLContext = True,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Document]:
    """
    Fetch a WeChat article URL and parse it into one or more Document objects.

    Args:
        url: WeChat article URL (e.g. https://mp.weixin.qq.com/s/...).
        doc_id: Optional document ID; defaults to URL or generated UUID.
        timeout: Request timeout in seconds (used only when ``client`` is not provided).
        user_agent: HTTP User-Agent header.
        verify: SSL verification for the httpx client: ``True`` (default CA bundle),
            ``False`` to disable, a path to a CA bundle, or a custom
            ``ssl.SSLContext`` — same semantics as :class:`httpx.AsyncClient`.
        client: Optional shared :class:`httpx.AsyncClient`; if omitted, a client is created with
            ``verify``, ``timeout``, and ``User-Agent`` and closed after the request.

    Returns:
        List of Document instances (typically one) with text and metadata (source_url, title).

    Raises:
        build_error(StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR): On invalid URL or fetch/parse failure.
    """
    url = (url or "").strip()
    if not _is_wechat_article_url(url):
        raise build_error(
            StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
            error_msg=f"Not a WeChat article URL: {url!r}",
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
            error_msg=f"WeChat article request failed: {status} for {url}",
            cause=e,
        ) from e
    except httpx.RequestError as e:
        raise build_error(
            StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
            error_msg=f"WeChat article fetch failed for {url}: {e}",
            cause=e,
        ) from e
    except Exception as e:
        raise build_error(
            StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
            error_msg=f"WeChat article fetch failed for {url}: {e}",
            cause=e,
        ) from e

    soup = _parse_html(html)
    title = _extract_title(soup)
    content_node = _extract_js_content(soup)
    if not content_node:
        raise build_error(
            StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
            error_msg=f"Could not find article content (js_content) in page: {url}",
        )
    text = _get_text_from_soup(content_node)
    if not text:
        raise build_error(
            StatusCode.RETRIEVAL_INDEXING_FETCH_ERROR,
            error_msg=f"Article content is empty after parsing: {url}",
        )

    effective_id = doc_id or url or str(uuid.uuid4())
    doc = Document(
        id_=effective_id,
        text=text,
        metadata={
            "source_url": url,
            "title": title or "(无标题)",
            "source_type": "wechat_article",
        },
    )
    logger.info("Parsed WeChat article: url=%s title=%s", url, title or "(无标题)")
    return [doc]


class WeChatArticleParser(Parser):
    """
    Parser for WeChat official account article URLs.
    Use AutoParser or parse_urls with AutoLinkParser for auto-dispatch by URL.
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
        """Parse a WeChat article URL into a list of Document (doc = URL string)."""
        timeout = kwargs.get("timeout", self.timeout)
        user_agent = kwargs.get("user_agent", self.user_agent)
        verify = kwargs.get("verify", self.verify)
        client = kwargs.get("client")
        return await parse_wechat_article_url(
            doc,
            doc_id=doc_id or doc,
            timeout=timeout,
            user_agent=user_agent,
            verify=verify,
            client=client,
        )

    def supports(self, doc: str) -> bool:
        """Return True if doc is a WeChat article URL."""
        return _is_wechat_article_url(doc)
