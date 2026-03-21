# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import base64
import os
import re
from html import unescape
from typing import Any, AsyncIterator, Dict
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import tool_logger

from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.deepagents.prompts.sections.tools import build_tool_card

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_REQUEST_HEADERS = {"User-Agent": _USER_AGENT}
_CHARSET_HEADER_RE = re.compile(r"charset=([^\s;]+)", flags=re.IGNORECASE)
_CHARSET_META_RE = re.compile(
    br"""<meta[^>]+charset=["']?\s*([A-Za-z0-9._-]+)""",
    flags=re.IGNORECASE,
)


def _http_request(method: str, url: str, **kwargs) -> requests.Response:
    """Try normal request first; retry without env proxies on ProxyError."""
    method_up = method.upper()
    try:
        if method_up == "GET":
            return requests.get(url, **kwargs)
        if method_up == "POST":
            return requests.post(url, **kwargs)
        return requests.request(method_up, url, **kwargs)
    except requests.exceptions.ProxyError:
        with requests.Session() as session:
            session.trust_env = False
            return session.request(method_up, url, **kwargs)


def _strip_tags(value: str) -> str:
    """Remove HTML tags and normalize whitespace."""
    value = re.sub(r"<[^>]+>", " ", value)
    return unescape(re.sub(r"\s+", " ", value)).strip()


def _decode_ddg_redirect(url: str) -> str:
    """Decode DuckDuckGo redirect URLs."""
    parsed = urlparse(url)
    if parsed.path != "/l/":
        return url
    query = parse_qs(parsed.query)
    target = query.get("uddg")
    if not target:
        return url
    return unquote(target[0])


class WebFreeSearchTool(Tool):
    """Free search via DuckDuckGo. Input query and return ranked URLs with snippets."""

    def __init__(self, language: str = "cn"):
        super().__init__(build_tool_card("free_search", "WebFreeSearchTool", language))

    @staticmethod
    def _decode_bing_redirect(url: str) -> str:
        """Decode Bing redirect URLs to extract the actual target URL."""
        parsed = urlparse(url)
        if "bing.com" not in parsed.netloc.lower() or parsed.path != "/ck/a":
            return url

        query = parse_qs(parsed.query)
        values = query.get("u")
        if not values:
            return url
        encoded = values[0]
        if not encoded:
            return url

        if encoded.startswith("a1"):
            payload = encoded[2:]
            padding = "=" * (-len(payload) % 4)
            try:
                decoded = base64.urlsafe_b64decode((payload + padding).encode("utf-8")).decode(
                    "utf-8", errors="ignore"
                )
                if decoded.startswith(("http://", "https://")):
                    return decoded
            except Exception:
                return url
        elif encoded.startswith(("http://", "https://")):
            return encoded

        return url

    @staticmethod
    def _is_ddg_challenge_page(status_code: int, html: str) -> bool:
        """Check if the response is a DuckDuckGo anti-bot challenge page."""
        if status_code in {202, 418, 429, 503}:
            return True
        text = (html or "").lower()
        markers = [
            "/anomaly.js",
            "challenge-form",
            "duckduckgo.com/anomaly.js",
        ]
        return any(marker in text for marker in markers)

    @staticmethod
    def _search_duckduckgo_sync(query: str, max_results: int, timeout_seconds: int) -> list[dict[str, str]]:
        """Search DuckDuckGo HTML endpoint and parse results synchronously."""
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        response = _http_request("GET", url, headers=_REQUEST_HEADERS, timeout=timeout_seconds)
        if WebFreeSearchTool._is_ddg_challenge_page(response.status_code, response.text):
            raise build_error(StatusCode.TOOL_WEB_SEARCH_ENGINE_ERROR, engine="duckduckgo",
                              reason="anti-bot challenge page returned")
        if response.status_code != 200:
            raise build_error(StatusCode.TOOL_WEB_SEARCH_ENGINE_ERROR, engine="duckduckgo",
                              reason=f"non-200 status: {response.status_code}")
        response.raise_for_status()
        html = response.text

        links = re.findall(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippets = re.findall(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>|<div[^>]+class="result__snippet"[^>]*>(.*?)</div>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )

        rows: list[dict[str, str]] = []
        for index, (href, title_raw) in enumerate(links[:max_results]):
            snippet_raw = ""
            if index < len(snippets):
                snippet_raw = snippets[index][0] or snippets[index][1] or ""
            rows.append(
                {
                    "title": _strip_tags(title_raw) or f"Result {index + 1}",
                    "url": _decode_ddg_redirect(href),
                    "snippet": _strip_tags(snippet_raw),
                }
            )
        return rows

    @staticmethod
    def _search_duckduckgo_via_jina_sync(
            query: str, max_results: int, timeout_seconds: int
    ) -> list[dict[str, str]]:
        """Search DuckDuckGo via jina.ai proxy and parse markdown results."""
        url = f"https://r.jina.ai/http://duckduckgo.com/html/?q={quote_plus(query)}"
        response = _http_request("GET", url, headers=_REQUEST_HEADERS, timeout=timeout_seconds)
        response.raise_for_status()
        text = response.text or ""

        # Parse markdown links rendered by r.jina.ai.
        matches = re.findall(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)", text, flags=re.IGNORECASE)

        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for title_raw, href in matches:
            title = _strip_tags(title_raw)
            if not title or title.startswith("Image "):
                continue
            decoded = _decode_ddg_redirect(href)
            parsed = urlparse(decoded)
            if not parsed.scheme.startswith("http"):
                continue
            # Drop DuckDuckGo navigation/self links.
            if "duckduckgo.com" in parsed.netloc.lower():
                continue
            if decoded in seen:
                continue
            seen.add(decoded)
            rows.append({"title": title, "url": decoded, "snippet": ""})
            if len(rows) >= max_results:
                break
        return rows

    @staticmethod
    def _search_bing_sync(query: str, max_results: int, timeout_seconds: int) -> list[dict[str, str]]:
        """Search Bing and parse search results synchronously."""
        url = f"https://www.bing.com/search?q={quote_plus(query)}"
        response = _http_request("GET", url, headers=_REQUEST_HEADERS, timeout=timeout_seconds)
        response.raise_for_status()
        html = response.text

        blocks = re.findall(
            r'<li[^>]+class="[^"]*\bb_algo\b[^"]*"[^>]*>(.*?)</li>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        rows: list[dict[str, str]] = []
        seen: set[str] = set()

        for block in blocks:
            title_match = re.search(
                r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                block,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if not title_match:
                continue
            href_raw = unescape(title_match.group(1))
            href = WebFreeSearchTool._decode_bing_redirect(href_raw)
            title = _strip_tags(title_match.group(2))
            if not href or href in seen:
                continue
            seen.add(href)
            snippet_match = re.search(r"<p>(.*?)</p>", block, flags=re.IGNORECASE | re.DOTALL)
            snippet = _strip_tags(snippet_match.group(1)) if snippet_match else ""
            rows.append({"title": title or f"Result {len(rows) + 1}", "url": href, "snippet": snippet})
            if len(rows) >= max_results:
                break

        return rows

    @staticmethod
    def _search_free_sync(
            query: str, max_results: int, timeout_seconds: int
    ) -> tuple[str, list[dict[str, str]]]:
        """Search using multiple free search engines with fallback."""
        errors: list[str] = []
        engines = [
            ("duckduckgo", WebFreeSearchTool._search_duckduckgo_sync),
            ("duckduckgo-jina", WebFreeSearchTool._search_duckduckgo_via_jina_sync),
            ("bing", WebFreeSearchTool._search_bing_sync),
        ]
        for engine_name, runner in engines:
            try:
                rows = runner(query, max_results, timeout_seconds)
            except Exception as exc:
                errors.append(f"{engine_name}: {exc}")
                continue
            if rows:
                return engine_name, rows
            errors.append(f"{engine_name}: empty result")
        raise build_error(StatusCode.TOOL_WEB_SEARCH_ALL_ENGINES_FAILED, errors=" | ".join(errors))

    @staticmethod
    def _engine_display_name(engine: str) -> str:
        """Get human-readable engine name."""
        mapping = {
            "duckduckgo": "DuckDuckGo",
            "duckduckgo-jina": "DuckDuckGo (via jina.ai)",
            "bing": "Bing",
        }
        return mapping.get(engine, engine)

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> str:
        """Invoke the free web search tool.

        Args:
            inputs: A dictionary containing the following keys:
                - query: The search query string.
                - max_results: Maximum number of results to return (default: 8, range: 1-20).
                - timeout_seconds: Request timeout in seconds (default: 20, range: 5-60).
            **kwargs: Additional keyword arguments (unused).

        Returns:
            A formatted string containing search results with titles, URLs, and snippets.
        """
        query = str(inputs.get("query", "") or "").strip()
        max_results = int(inputs.get("max_results", 8) or 8)
        timeout_seconds = int(inputs.get("timeout_seconds", 20) or 20)

        if not query:
            return "[ERROR]: query cannot be empty."

        max_results = max(1, min(max_results, 20))
        timeout_seconds = max(5, min(timeout_seconds, 60))
        try:
            engine_used, rows = await asyncio.to_thread(
                WebFreeSearchTool._search_free_sync, query, max_results, timeout_seconds
            )
        except Exception as exc:
            return f"[ERROR]: free search failed: {exc}"

        if not rows:
            return f"No search results for: {query}"

        lines = [f"Free search results ({WebFreeSearchTool._engine_display_name(engine_used)}) for: {query}"]
        for idx, row in enumerate(rows, 1):
            lines.append(f"{idx}. {row['title']}")
            lines.append(f"   URL: {row['url']}")
            if row.get("snippet"):
                lines.append(f"   Snippet: {row['snippet']}")
        return "\n".join(lines)

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        """Stream is not supported for this tool."""
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)


class WebPaidSearchTool(Tool):
    """Paid search via Perplexity/SERPER/JINA. Support provider=auto|perplexity|serper|jina."""

    def __init__(self, language: str = "cn"):
        super().__init__(build_tool_card("paid_search", "WebPaidSearchTool", language))

    @staticmethod
    def _jina_search_sync(query: str, timeout_seconds: int) -> dict[str, Any]:
        """Search using Jina AI DeepSearch API synchronously."""
        jina_key = os.environ.get("JINA_API_KEY", "")
        if not jina_key:
            raise build_error(StatusCode.TOOL_WEB_API_KEY_NOT_SET, key_name="JINA_API_KEY")

        payload = {
            "model": "jina-deepsearch-v1",
            "messages": [{"role": "user", "content": query}],
            "stream": False,
            "reasoning_effort": "low",
        }
        response = _http_request(
            "POST",
            "https://deepsearch.jina.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {jina_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()

        answer = ""
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            answer = choices[0].get("message", {}).get("content", "")
        urls = re.findall(r"https?://[^\s)\]>\"']+", answer or "")
        return {"provider": "jina", "answer": (answer or "").strip(), "urls": urls}

    @staticmethod
    def _serper_search_sync(query: str, max_results: int, timeout_seconds: int) -> dict[str, Any]:
        """Search using Serper (Google Search API) synchronously."""
        serper_key = os.environ.get("SERPER_API_KEY", "")
        if not serper_key:
            raise build_error(StatusCode.TOOL_WEB_API_KEY_NOT_SET, key_name="SERPER_API_KEY")

        response = _http_request(
            "POST",
            "https://google.serper.dev/search",
            headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
            json={"q": query, "num": max_results},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        urls: list[str] = []
        organic = data.get("organic", [])
        if isinstance(organic, list):
            for item in organic[:max_results]:
                if isinstance(item, dict) and item.get("link"):
                    urls.append(str(item["link"]))
        return {"provider": "serper", "answer": "", "urls": urls}

    @staticmethod
    def _parse_perplexity_citations(data: dict[str, Any]) -> list[str]:
        """Extract citation URLs from Perplexity API response."""
        for key in ("citations", "search_results", "web_search_results", "sources"):
            entries = data.get(key)
            if not isinstance(entries, list):
                continue
            urls: list[str] = []
            for item in entries:
                if isinstance(item, str):
                    urls.append(item)
                elif isinstance(item, dict):
                    maybe_url = item.get("url") or item.get("link") or item.get("source_url")
                    if maybe_url:
                        urls.append(str(maybe_url))
            if urls:
                return urls
        return []

    @staticmethod
    def _perplexity_search_sync(query: str, max_results: int, timeout_seconds: int) -> dict[str, Any]:
        """Search using Perplexity AI synchronously."""
        perplexity_key = os.environ.get("PERPLEXITY_API_KEY", "")
        if not perplexity_key:
            raise build_error(StatusCode.TOOL_WEB_API_KEY_NOT_SET, key_name="PERPLEXITY_API_KEY")

        payload = {
            "model": os.environ.get("PPLX_MODEL", "sonar-pro"),
            "messages": [
                {"role": "system", "content": "Provide concise answer and include citations."},
                {"role": "user", "content": query},
            ],
            "max_tokens": 1024,
            "temperature": 0.2,
            "stream": False,
        }
        response = _http_request(
            "POST",
            os.environ.get("PPLX_API_URL", "https://api.perplexity.ai/chat/completions"),
            headers={"Authorization": f"Bearer {perplexity_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()

        answer = ""
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            answer = choices[0].get("message", {}).get("content", "")

        return {
            "provider": "perplexity",
            "answer": (answer or "").strip(),
            "urls": WebPaidSearchTool._parse_perplexity_citations(data)[:max_results],
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> str:
        """Invoke the paid web search tool.

        Args:
            inputs: A dictionary containing the following keys:
                - query: The search query string.
                - provider: Search provider ("auto", "perplexity", "serper", or "jina").
                - max_results: Maximum number of results to return (default: 8, range: 1-20).
                - timeout_seconds: Request timeout in seconds (default: 45, range: 10-120).
            **kwargs: Additional keyword arguments (unused).

        Returns:
            A formatted string containing search results with answers and URLs.
        """
        query = str(inputs.get("query", "") or "").strip()
        provider = str(inputs.get("provider", "auto") or "auto").strip().lower()
        max_results = int(inputs.get("max_results", 8) or 8)
        timeout_seconds = int(inputs.get("timeout_seconds", 45) or 45)

        if not query:
            return "[ERROR]: query cannot be empty."

        if provider not in {"auto", "jina", "serper", "perplexity"}:
            return "[ERROR]: provider must be one of auto|jina|serper|perplexity."

        timeout_seconds = max(10, min(timeout_seconds, 120))
        max_results = max(1, min(max_results, 20))

        runners = {
            "jina": lambda: WebPaidSearchTool._jina_search_sync(query=query, timeout_seconds=timeout_seconds),
            "serper": lambda: WebPaidSearchTool._serper_search_sync(
                query=query, max_results=max_results, timeout_seconds=timeout_seconds
            ),
            "perplexity": lambda: WebPaidSearchTool._perplexity_search_sync(
                query=query, max_results=max_results, timeout_seconds=timeout_seconds
            ),
        }
        order = [provider] if provider != "auto" else ["perplexity", "serper", "jina"]

        errors: list[str] = []
        for name in order:
            try:
                runner_process = runners.get(name)
                if runner_process is None:
                    errors.append(f"{name}: runner not found")
                    continue
                result = await asyncio.to_thread(runner_process)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                continue

            answer = str(result.get("answer", "") or "").strip()
            urls = [str(u) for u in (result.get("urls", []) or []) if u][:max_results]
            lines = [f"Paid search provider: {name}"]
            if answer:
                lines.append("Answer:")
                lines.append(answer)
            if urls:
                lines.append("URLs:")
                for idx, url in enumerate(urls, 1):
                    lines.append(f"{idx}. {url}")
            if not answer and not urls:
                lines.append("No usable result payload.")
            return "\n".join(lines)

        return "[ERROR]: paid search failed. " + " | ".join(errors)

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        """Stream is not supported for this tool."""
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)


class WebFetchWebpageTool(Tool):
    """Fetch webpage text content from URL. Returns status/title/plain text content."""

    def __init__(self, language: str = "cn"):
        super().__init__(build_tool_card("fetch_webpage", "WebFetchWebpageTool", language))

    @staticmethod
    def _extract_declared_charset(response: requests.Response) -> str:
        """Extract charset from response headers or HTML meta tags."""
        content_type = response.headers.get("Content-Type", "") or ""
        header_match = _CHARSET_HEADER_RE.search(content_type)
        if header_match:
            return header_match.group(1).strip().strip("\"'")

        head_bytes = (response.content or b"")[:4096]
        meta_match = _CHARSET_META_RE.search(head_bytes)
        if meta_match:
            try:
                return meta_match.group(1).decode("ascii", errors="ignore").strip()
            except Exception:
                return ""
        return ""

    @staticmethod
    def _decode_response_text(response: requests.Response) -> str:
        """Decode response content with proper charset detection."""
        raw = response.content or b""
        if not raw:
            return ""

        declared = (WebFetchWebpageTool._extract_declared_charset(response) or "").lower()
        response_encoding = (response.encoding or "").strip().lower()
        apparent = (response.apparent_encoding or "").strip().lower()

        # Prefer explicit non-latin declaration first; then utf-8; then heuristics.
        candidates: list[str] = []
        if declared and declared not in {"iso-8859-1", "latin-1", "latin1"}:
            candidates.append(declared)

        candidates.extend(
            [
                "utf-8",
                apparent,
                response_encoding,
                "gb18030",
                "big5",
                "shift_jis",
                "cp1252",
                "iso-8859-1",
            ]
        )

        seen: set[str] = set()
        for enc in candidates:
            enc = (enc or "").strip().lower()
            if not enc or enc in seen:
                continue
            seen.add(enc)
            try:
                return raw.decode(enc, errors="strict")
            except Exception:
                tool_logger.warning(f"Failed to decode with {enc}")
                continue

        # Last-resort fallback.
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _fetch_via_jina_reader_sync(url: str, timeout_seconds: int) -> dict[str, str | int]:
        """Fetch webpage content via jina.ai reader proxy."""
        reader_url = f"https://r.jina.ai/{url}"
        response = _http_request("GET", reader_url, headers=_REQUEST_HEADERS, timeout=timeout_seconds)
        response.raise_for_status()
        return {
            "url": url,
            "status_code": response.status_code,
            "title": "",
            "content": WebFetchWebpageTool._decode_response_text(response).strip(),
        }

    @staticmethod
    def _fetch_webpage_sync(url: str, timeout_seconds: int) -> dict[str, str | int]:
        """Fetch webpage content synchronously with fallback to jina.ai reader."""
        response = _http_request("GET", url, headers=_REQUEST_HEADERS, timeout=timeout_seconds)
        if response.status_code in {401, 403, 429}:
            return WebFetchWebpageTool._fetch_via_jina_reader_sync(url, timeout_seconds)
        response.raise_for_status()

        text = WebFetchWebpageTool._decode_response_text(response)
        content_type = response.headers.get("Content-Type", "")
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
        title = _strip_tags(title_match.group(1)) if title_match else ""

        if "html" in content_type.lower():
            text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
            text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.IGNORECASE | re.DOTALL)
            text = _strip_tags(text)
        else:
            text = re.sub(r"\s+", " ", text).strip()

        return {
            "url": response.url,
            "status_code": response.status_code,
            "title": title,
            "content": text,
        }

    @staticmethod
    def _clip_text(value: str, max_chars: int) -> str:
        """Clip text to maximum length with truncation indicator."""
        if len(value) <= max_chars:
            return value
        return f"{value[:max_chars]}\n...[truncated]"

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize URL by decoding redirects and ensuring protocol."""
        raw = (url or "").strip()
        if not raw:
            return raw
        decoded = _decode_ddg_redirect(raw)
        if decoded.startswith(("http://", "https://")):
            return decoded
        return f"https://{decoded}"

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> str:
        """Invoke the webpage fetch tool.

        Args:
            inputs: A dictionary containing the following keys:
                - url: The URL to fetch.
                - max_chars: Maximum characters to return (default: 12000, range: 500-50000).
                - timeout_seconds: Request timeout in seconds (default: 30, range: 5-120).
            **kwargs: Additional keyword arguments (unused).

        Returns:
            A formatted string containing the webpage URL, status, title, and content.
        """
        url = WebFetchWebpageTool._normalize_url(str(inputs.get("url", "") or ""))
        max_chars = int(inputs.get("max_chars", 12000) or 12000)
        timeout_seconds = int(inputs.get("timeout_seconds", 30) or 30)

        if not url:
            return "[ERROR]: url cannot be empty."

        max_chars = max(500, min(max_chars, 50000))
        timeout_seconds = max(5, min(timeout_seconds, 120))

        try:
            data = await asyncio.to_thread(WebFetchWebpageTool._fetch_webpage_sync, url, timeout_seconds)
        except Exception as exc:
            return f"[ERROR]: failed to fetch webpage: {exc}"

        lines = [
            f"URL: {data.get('url', url)}",
            f"Status: {data.get('status_code', '')}",
        ]
        if data.get("title"):
            lines.append(f"Title: {data['title']}")
        lines.append("Content:")
        lines.append(WebFetchWebpageTool._clip_text(str(data.get("content", "") or ""), max_chars) or "[empty]")
        return "\n".join(lines)

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        """Stream is not supported for this tool."""
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)
