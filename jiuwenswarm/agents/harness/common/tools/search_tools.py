# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Search tools implemented with openjiuwen @tool style."""

from __future__ import annotations

import asyncio
import base64
import os
import re
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import urllib3
from openjiuwen.core.foundation.tool import tool

from jiuwenswarm.agents.harness.common.tools.ssl_config import get_requests_verify
from jiuwenswarm.common.http_proxy_config import requests_request
from jiuwenswarm.common.local_env_config import get_local_config

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_REQUEST_HEADERS = {"User-Agent": _USER_AGENT}
_FREE_SEARCH_DDG_ENABLED_ENV = "FREE_SEARCH_DDG_ENABLED"
_FREE_SEARCH_BING_ENABLED_ENV = "FREE_SEARCH_BING_ENABLED"
_FREE_SEARCH_PROXY_URL_ENV = "FREE_SEARCH_PROXY_URL"
_FREE_SEARCH_SSL_VERIFY_ENV = "FREE_SEARCH_SSL_VERIFY"
_FREE_SEARCH_DDG_URL_ENV = "FREE_SEARCH_DDG_URL"
_FREE_SEARCH_DEFAULT_NO_PROXY = (
    "127.0.0.1,.huawei.com,localhost,local,.local,10.155.97.247,.myhuaweicloud.com"
)

_PETAL_SEARCH_URL_ENV = "PETAL_SEARCH_URL"
_PETAL_SEARCH_HEADERS_ENV = "PETAL_SEARCH_HEADERS"
_PETAL_MAX_TITLE_LEN = 2000
_PETAL_MAX_URL_LEN = 2048

_PAID_PROVIDER_KEY_ENVS: dict[str, str] = {
    "bocha": "BOCHA_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "serper": "SERPER_API_KEY",
    "jina": "JINA_API_KEY",
}
_DEFAULT_PAID_PROVIDER_ORDER: tuple[str, ...] = ("petal", "bocha")
_KNOWN_PAID_PROVIDERS = frozenset(_PAID_PROVIDER_KEY_ENVS.keys() | {"petal"})

_ENGINE_ALIAS_MAP: dict[str, str] = {
    "duckduckgo": "duckduckgo", "ddg": "duckduckgo",
    "谷歌": "google", "google": "google",
    "必应": "bing", "bing": "bing",
    "百度": "baidu", "baidu": "baidu",
    "花瓣": "petal", "petal": "petal",
    "博查": "bocha", "bocha": "bocha",
    "360": "360", "好搜": "360", "so": "360",
    "搜狗": "sogou", "sogou": "sogou",
    "头条": "toutiao", "今日头条": "toutiao",
}

_PROVIDER_TO_ENGINE: dict[str, str] = {
    "petal": "petal",
    "bocha": "bocha",
    "tavily": "tavily",
    "perplexity": "perplexity",
    "serper": "serper",
    "jina": "jina",
    "duckduckgo": "duckduckgo",
    "duckduckgo-jina": "duckduckgo",
    "bing": "bing",
}


def _get_free_search_proxy_url() -> str:
    return str(get_local_config(_FREE_SEARCH_PROXY_URL_ENV, "") or "").strip()


def _env_bool(name: str, default: bool = True) -> bool:
    raw = str(get_local_config(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "enabled"}


def _env_flag(name: str, default: bool = False) -> bool:
    return _env_bool(name, default=default)


def _configured_paid_providers() -> list[str]:
    order = list(_DEFAULT_PAID_PROVIDER_ORDER)
    petal_url = str(os.environ.get(_PETAL_SEARCH_URL_ENV, "") or "").strip()
    petal_headers = str(os.environ.get(_PETAL_SEARCH_HEADERS_ENV, "") or "").strip()
    if not (petal_url and petal_headers):
        order = [p for p in order if p != "petal"]
    for name in _PAID_PROVIDER_KEY_ENVS:
        if name not in order:
            order.append(name)
    return [p for p in order if _paid_provider_available(p)]


def _paid_provider_available(name: str) -> bool:
    if name == "petal":
        petal_url = str(os.environ.get(_PETAL_SEARCH_URL_ENV, "") or "").strip()
        petal_headers = str(os.environ.get(_PETAL_SEARCH_HEADERS_ENV, "") or "").strip()
        return bool(petal_url and petal_headers)
    key = _PAID_PROVIDER_KEY_ENVS.get(name, "")
    return bool(key and str(os.environ.get(key, "") or "").strip())


def _paid_provider_skip_reason(name: str) -> str:
    if name == "petal":
        petal_url = str(os.environ.get(_PETAL_SEARCH_URL_ENV, "") or "").strip()
        petal_headers = str(os.environ.get(_PETAL_SEARCH_HEADERS_ENV, "") or "").strip()
        if not petal_url:
            return "PETAL_SEARCH_URL not set"
        if not petal_headers:
            return "PETAL_SEARCH_HEADERS not set"
        return "unknown"
    key = _PAID_PROVIDER_KEY_ENVS.get(name, "")
    if not key:
        return f"unknown provider: {name}"
    return f"{key} not set"


def _detect_requested_engine(query: str) -> str | None:
    query_lower = query.lower()
    for alias, engine in _ENGINE_ALIAS_MAP.items():
        if alias.isascii():
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", query_lower):
                return engine
        elif alias in query_lower:
            return engine
    return None


def _generate_engine_mismatch_warning(
    query: str,
    actual_provider: str,
) -> str | None:
    requested_engine = _detect_requested_engine(query)
    if not requested_engine:
        return None
    actual_engine = _PROVIDER_TO_ENGINE.get(actual_provider, actual_provider)
    if requested_engine == actual_engine:
        return None
    return (
        f"⚠️ 用户请求使用 {requested_engine} 搜索，但该引擎不可用，"
        f"已自动切换至 {actual_provider}。"
    )


def _free_search_engines() -> list[str]:
    engines = []
    if _env_flag(_FREE_SEARCH_DDG_ENABLED_ENV, default=False):
        engines.extend(["duckduckgo", "duckduckgo-jina"])
    if _env_flag(_FREE_SEARCH_BING_ENABLED_ENV, default=False):
        engines.append("bing")
    return engines


def _free_search_ssl_verify() -> bool:
    return _env_bool(_FREE_SEARCH_SSL_VERIFY_ENV, default=False)


def _disable_insecure_request_warning() -> None:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _duckduckgo_search_url(query: str) -> str:
    base_url = (
        get_local_config(_FREE_SEARCH_DDG_URL_ENV, "https://html.duckduckgo.com/html/")
        or "https://html.duckduckgo.com/html/"
    ).strip()
    separator = "&" if "?" in base_url else "?"
    return f"{base_url.rstrip('?&')}{separator}q={quote_plus(query)}"


def _no_proxy_entries() -> list[str]:
    configured = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or _FREE_SEARCH_DEFAULT_NO_PROXY
    return [entry.strip().lower() for entry in configured.split(",") if entry.strip()]


def _should_bypass_free_search_proxy(url: str) -> bool:
    proxy_url = _get_free_search_proxy_url()
    if not proxy_url:
        return True
    hostname = (urlparse(url).hostname or "").lower()
    if not hostname:
        return False
    for entry in _no_proxy_entries():
        if entry == "*":
            return True
        if entry.startswith(".") and (hostname == entry[1:] or hostname.endswith(entry)):
            return True
        if hostname == entry or hostname.endswith(f".{entry}"):
            return True
    return False


def _apply_free_search_proxy(url: str, kwargs: dict[str, Any]) -> bool:
    proxy_url = _get_free_search_proxy_url()
    if not proxy_url or _should_bypass_free_search_proxy(url):
        return False
    kwargs.setdefault("proxies", {"http": proxy_url, "https": proxy_url})
    return True


def _http_request(method: str, url: str, **kwargs) -> Any:
    """Issue HTTP via overlay-aware proxy helpers; free-search proxy still applied."""
    kwargs.setdefault("verify", get_requests_verify())
    method_up = method.upper()
    _apply_free_search_proxy(url, kwargs)
    if "verify" not in kwargs:
        kwargs["verify"] = _free_search_ssl_verify()
        if kwargs["verify"] is False:
            _disable_insecure_request_warning()
    return requests_request(method_up, url, **kwargs)


def _strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return unescape(re.sub(r"\s+", " ", value)).strip()


def _decode_ddg_redirect(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path != "/l/":
        return url
    query = parse_qs(parsed.query)
    target = query.get("uddg")
    if not target:
        return url
    return unquote(target[0])


def _decode_bing_redirect(url: str) -> str:
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


def _is_ddg_challenge_page(status_code: int, html: str) -> bool:
    if status_code in {202, 418, 429, 503}:
        return True
    text = (html or "").lower()
    markers = [
        "/anomaly.js",
        "challenge-form",
        "duckduckgo.com/anomaly.js",
    ]
    return any(marker in text for marker in markers)


def _search_duckduckgo_sync(query: str, max_results: int, timeout_seconds: int) -> list[dict[str, str]]:
    url = _duckduckgo_search_url(query)
    response = _http_request("GET", url, headers=_REQUEST_HEADERS, timeout=timeout_seconds)
    if _is_ddg_challenge_page(response.status_code, response.text):
        raise RuntimeError("DuckDuckGo anti-bot challenge page returned")
    if response.status_code != 200:
        raise RuntimeError(f"DuckDuckGo returned non-200 status: {response.status_code}")
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


def _search_duckduckgo_via_jina_sync(
    query: str, max_results: int, timeout_seconds: int
) -> list[dict[str, str]]:
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


def _search_bing_sync(query: str, max_results: int, timeout_seconds: int) -> list[dict[str, str]]:
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
        href = _decode_bing_redirect(href_raw)
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


def _search_free_sync(
    query: str, max_results: int, timeout_seconds: int
) -> tuple[str, list[dict[str, str]]]:
    errors: list[str] = []
    engines = []
    if _env_flag(_FREE_SEARCH_DDG_ENABLED_ENV, default=False):
        engines.extend([
            ("duckduckgo", _search_duckduckgo_sync),
            ("duckduckgo-jina", _search_duckduckgo_via_jina_sync),
        ])
    if _env_flag(_FREE_SEARCH_BING_ENABLED_ENV, default=False):
        engines.append(("bing", _search_bing_sync))
    if not engines:
        raise RuntimeError("all free search engines are disabled")
    for engine_name, runner in engines:
        try:
            rows = runner(query, max_results, timeout_seconds)
        except Exception as exc:
            errors.append(f"{engine_name}: {exc}")
            continue
        if rows:
            return engine_name, rows
        errors.append(f"{engine_name}: empty result")
    raise RuntimeError(" | ".join(errors))


def _engine_display_name(engine: str) -> str:
    mapping = {
        "duckduckgo": "DuckDuckGo",
        "duckduckgo-jina": "DuckDuckGo (via jina.ai)",
        "bing": "Bing",
    }
    return mapping.get(engine, engine)


def _parse_perplexity_citations(data: dict[str, Any]) -> list[str]:
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


def _perplexity_search_sync(query: str, max_results: int, timeout_seconds: int) -> dict[str, Any]:
    perplexity_key = str(get_local_config("PERPLEXITY_API_KEY", "") or "")
    if not perplexity_key:
        raise ValueError("PERPLEXITY_API_KEY is not set")

    payload = {
        "model": str(get_local_config("PPLX_MODEL", "sonar-pro") or "sonar-pro"),
        "messages": [
            {"role": "system", "content": "Provide concise answer and include citations."},
            {"role": "user", "content": query},
        ],
        "max_tokens": 1024,
        "temperature": 0.2,
        "stream": False,
    }
    pplx_url = get_local_config(
        "PPLX_API_URL",
        "https://api.perplexity.ai/chat/completions",
    ) or "https://api.perplexity.ai/chat/completions"
    response = _http_request(
        "POST",
        str(pplx_url),
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
        "urls": _parse_perplexity_citations(data)[:max_results],
    }


def _extract_bocha_urls(data: dict[str, Any], max_results: int) -> list[str]:
    candidates: list[Any] = []
    for container in (
        data.get("data", {}).get("webPages", {}).get("value"),
        data.get("webPages", {}).get("value"),
        data.get("data", {}).get("webPages"),
        data.get("webPages"),
        data.get("data", {}).get("results"),
        data.get("results"),
    ):
        if isinstance(container, list):
            candidates = container
            break

    urls: list[str] = []
    for item in candidates[:max_results]:
        if not isinstance(item, dict):
            continue
        maybe_url = item.get("url") or item.get("link")
        if maybe_url:
            urls.append(str(maybe_url))
    return urls


def _extract_bocha_answer(data: dict[str, Any]) -> str:
    candidates = [
        data.get("summary"),
        data.get("answer"),
        data.get("data", {}).get("summary"),
        data.get("data", {}).get("answer"),
        data.get("data", {}).get("message"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()

    web_pages = data.get("data", {}).get("webPages", {})
    if isinstance(web_pages, dict):
        value = web_pages.get("value")
        if isinstance(value, list):
            snippets: list[str] = []
            for item in value[:3]:
                if not isinstance(item, dict):
                    continue
                snippet = item.get("summary") or item.get("snippet")
                if isinstance(snippet, str) and snippet.strip():
                    snippets.append(snippet.strip())
            if snippets:
                return "\n\n".join(snippets[:3])
    return ""


def _bocha_search_sync(query: str, max_results: int, timeout_seconds: int) -> dict[str, Any]:
    bocha_key = str(get_local_config("BOCHA_API_KEY", "") or "")
    if not bocha_key:
        raise ValueError("BOCHA_API_KEY is not set")

    bocha_url = get_local_config(
        "BOCHA_API_URL",
        "https://api.bocha.cn/v1/web-search",
    ) or "https://api.bocha.cn/v1/web-search"
    response = _http_request(
        "POST",
        str(bocha_url),
        headers={"Authorization": f"Bearer {bocha_key}", "Content-Type": "application/json"},
        json={"query": query, "summary": True, "count": max_results},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    return {
        "provider": "bocha",
        "answer": _extract_bocha_answer(data),
        "urls": _extract_bocha_urls(data, max_results),
    }


def _serper_search_sync(query: str, max_results: int, timeout_seconds: int) -> dict[str, Any]:
    serper_key = str(get_local_config("SERPER_API_KEY", "") or "")
    if not serper_key:
        raise ValueError("SERPER_API_KEY is not set")

    serper_url = get_local_config(
        "SERPER_API_URL",
        "https://google.serper.dev/search",
    ) or "https://google.serper.dev/search"
    response = _http_request(
        "POST",
        str(serper_url),
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


def _jina_search_sync(query: str, timeout_seconds: int) -> dict[str, Any]:
    jina_key = str(get_local_config("JINA_API_KEY", "") or "")
    if not jina_key:
        raise ValueError("JINA_API_KEY is not set")

    payload = {
        "model": "jina-deepsearch-v1",
        "messages": [{"role": "user", "content": query}],
        "stream": False,
        "reasoning_effort": "low",
    }
    jina_url = get_local_config(
        "JINA_API_URL",
        "https://deepsearch.jina.ai/v1/chat/completions",
    ) or "https://deepsearch.jina.ai/v1/chat/completions"
    response = _http_request(
        "POST",
        str(jina_url),
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


def _parse_default_headers(raw: str) -> dict[str, str]:
    if not raw or not raw.strip():
        return {}
    import json as _json
    raw_stripped = raw.strip()
    if raw_stripped.startswith("{"):
        try:
            parsed = _json.loads(raw_stripped)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items() if v is not None}
        except (_json.JSONDecodeError, ValueError):
            pass
    result: dict[str, str] = {}
    for pair in raw_stripped.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _resolve_petal_search_url() -> str:
    petal_url = str(os.environ.get(_PETAL_SEARCH_URL_ENV, "") or "").strip()
    if not petal_url:
        raise ValueError("PETAL_SEARCH_URL is not set")
    return petal_url


def _load_petal_default_headers() -> dict[str, str]:
    raw = str(os.environ.get(_PETAL_SEARCH_HEADERS_ENV, "") or "").strip()
    header_map = _parse_default_headers(raw)
    if not header_map:
        raise ValueError("PETAL_SEARCH_HEADERS is not set")
    return header_map


def _extract_petal_records(data: dict[str, Any], max_results: int) -> list[dict[str, str]]:
    items = []
    for container in (
        data.get("data", {}).get("webPages", {}).get("value"),
        data.get("webPages", {}).get("value"),
        data.get("data", {}).get("webPages"),
        data.get("webPages"),
        data.get("data", {}).get("results"),
        data.get("results"),
    ):
        if isinstance(container, list):
            items = container
            break

    rows: list[dict[str, str]] = []
    for item in items[:max_results]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("name", "") or item.get("title", "") or "")[:_PETAL_MAX_TITLE_LEN]
        url = str(item.get("url", "") or item.get("link", "") or "")[:_PETAL_MAX_URL_LEN]
        snippet = str(item.get("snippet", "") or item.get("summary", "") or "")
        if url:
            rows.append({"title": title, "url": url, "snippet": snippet})
    return rows


def _extract_petal_answer(data: dict[str, Any]) -> str:
    candidates = [
        data.get("data", {}).get("answer"),
        data.get("data", {}).get("summary"),
        data.get("answer"),
        data.get("summary"),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _petal_search_sync(query: str, max_results: int, timeout_seconds: int) -> dict[str, Any]:
    petal_url = _resolve_petal_search_url()
    headers = _load_petal_default_headers()
    headers["Content-Type"] = "application/json"

    payload = {"query": query, "num": max_results}
    response = _http_request(
        "POST",
        petal_url,
        headers=headers,
        json=payload,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    records = _extract_petal_records(data, max_results)
    answer = _extract_petal_answer(data)
    urls = [r["url"] for r in records if r.get("url")]
    return {"provider": "petal", "answer": answer, "urls": urls, "records": records}


def _build_free_search_description() -> str:
    if _free_search_engines():
        return "Free search via DuckDuckGo/Bing. Input query and return ranked URLs with snippets."
    return ""


@tool(
    name="mcp_free_search",
    description=(
        _build_free_search_description()
        or "Free search via DuckDuckGo. Input query and return ranked URLs with snippets."
    ),
)
async def mcp_free_search(query: str, max_results: int = 8, timeout_seconds: int = 20) -> str:
    query = (query or "").strip()
    if not query:
        return "[ERROR]: query cannot be empty."

    max_results = max(1, min(max_results, 20))
    timeout_seconds = max(5, min(timeout_seconds, 60))
    try:
        engine_used, rows = await asyncio.to_thread(
            _search_free_sync, query, max_results, timeout_seconds
        )
    except Exception as exc:
        return f"[ERROR]: free search failed: {exc}"

    if not rows:
        return f"No search results for: {query}"

    lines = [f"Free search results ({_engine_display_name(engine_used)}) for: {query}"]
    warning = _generate_engine_mismatch_warning(query, engine_used)
    if warning:
        lines.append(warning)
    for idx, row in enumerate(rows, 1):
        lines.append(f"{idx}. {row['title']}")
        lines.append(f"   URL: {row['url']}")
        if row.get("snippet"):
            lines.append(f"   Snippet: {row['snippet']}")
    return "\n".join(lines)


def _build_paid_search_description() -> str:
    providers = ", ".join(_configured_paid_providers()) or "bocha, petal"
    has_free = bool(_free_search_engines())
    base = (
        f"Paid search via {providers}. "
        f"Support provider=auto|{providers.replace(', ', '|')}. "
        "search_source 可选，指定付费源名称，配合 provider 使用时优先使用指定源，不可用时返回明确错误。"
    )
    if has_free:
        base += " 若用户指定使用某搜索引擎（如 bing、duckduckgo），请在 query 开头包含该引擎名称，以便系统识别并在引擎不可用时向用户说明。"
    else:
        base += " 若用户指定使用某付费搜索引擎，请在 query 开头包含该引擎名称，以便系统识别并在引擎不可用时向用户说明。"
    return base


@tool(
    name="mcp_paid_search",
    description=_build_paid_search_description(),
)
async def mcp_paid_search(
    query: str,
    provider: str = "auto",
    search_source: str | None = None,
    max_results: int = 8,
    timeout_seconds: int = 45,
) -> str:
    query = (query or "").strip()
    if not query:
        return "[ERROR]: query cannot be empty."

    provider = (provider or "auto").strip().lower()
    preferred_source = None
    if search_source:
        raw = search_source.strip().lower()
        if raw in _KNOWN_PAID_PROVIDERS:
            preferred_source = raw

    all_valid = {"auto"} | _KNOWN_PAID_PROVIDERS
    if provider not in all_valid:
        return f"[ERROR]: provider must be one of auto|{'|'.join(sorted(_KNOWN_PAID_PROVIDERS))}."

    timeout_seconds = max(10, min(timeout_seconds, 120))
    max_results = max(1, min(max_results, 20))

    runners = {
        "petal": lambda: _petal_search_sync(
            query=query, max_results=max_results, timeout_seconds=timeout_seconds
        ),
        "bocha": lambda: _bocha_search_sync(
            query=query, max_results=max_results, timeout_seconds=timeout_seconds
        ),
        "jina": lambda: _jina_search_sync(query=query, timeout_seconds=timeout_seconds),
        "serper": lambda: _serper_search_sync(
            query=query, max_results=max_results, timeout_seconds=timeout_seconds
        ),
        "perplexity": lambda: _perplexity_search_sync(
            query=query, max_results=max_results, timeout_seconds=timeout_seconds
        ),
    }
    if provider != "auto":
        if not _paid_provider_available(provider):
            reason = _paid_provider_skip_reason(provider)
            return f"[ERROR]: requested provider '{provider}' unavailable ({reason})."
        order = [provider]
    else:
        order = _configured_paid_providers()
        if not order:
            return "[ERROR]: no paid search API keys configured."

    if preferred_source:
        if preferred_source in order:
            order = [preferred_source] + [p for p in order if p != preferred_source]
        elif provider == "auto":
            if not _paid_provider_available(preferred_source):
                reason = _paid_provider_skip_reason(preferred_source)
                return f"[ERROR]: requested source '{preferred_source}' unavailable ({reason})."
            order = [preferred_source] + order

    errors: list[str] = []
    for name in order:
        runner = runners.get(name)
        if runner is None:
            continue
        try:
            result = await asyncio.to_thread(runner)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            continue

        answer = str(result.get("answer", "") or "").strip()
        urls = [str(u) for u in (result.get("urls", []) or []) if u][:max_results]
        lines = [f"Paid search provider: {name}"]
        warning = _generate_engine_mismatch_warning(query, name)
        if warning:
            lines.append(warning)
        if answer:
            lines.append("Answer:")
            lines.append(answer)
        if urls:
            lines.append("URLs:")
            for idx, url in enumerate(urls, 1):
                lines.append(f"{idx}. {url}")
        if not answer and not urls:
            errors.append(f"{name}: no usable result payload")
            continue
        return "\n".join(lines)

    return "[ERROR]: paid search failed. " + " | ".join(errors)
