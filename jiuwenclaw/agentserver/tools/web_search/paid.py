# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Paid search providers (petal, tavily, perplexity, serper, jina, bocha)."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from jiuwenclaw.agentserver.tools.web_search.http_client import http_request
from jiuwenclaw.config import get_config

_PETAL_MAX_TITLE_LEN = 2000
_PETAL_MAX_URL_LEN = 2048
_PETAL_MAX_SUMMARY_LEN = 4000
_TAVILY_DEFAULT_API_URL = "https://api.tavily.com"
_TAVILY_MAX_CONTENT_LEN = 4000


def _resolve_petal_search_url() -> str:
    api_base = (
        os.environ.get("API_BASE")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE")
        or ""
    ).strip()
    if not api_base:
        raise ValueError("API_BASE is not set")
    trimmed = api_base.rstrip("/")
    if trimmed.lower().endswith("/v2"):
        trimmed = trimmed[:-3]
    trimmed = trimmed.rstrip("/")
    return f"{trimmed}/v1/ai-tools/web-search"


def _load_llm_default_headers() -> dict[str, str]:
    raw = os.environ.get("default_headers", "").strip()
    if not raw:
        raise ValueError("default_headers is not set")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"default_headers is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("default_headers must be a JSON object")
    return {str(k): str(v) for k, v in parsed.items() if v is not None}


def _petal_normalize_web_page_item(item: dict[str, Any]) -> dict[str, str]:
    raw_url = item.get("url") or item.get("link") or item.get("source_url") or ""
    title = (item.get("title") or item.get("name") or "").strip()
    summary = (item.get("summary") or "").strip()
    return {
        "title": title[:_PETAL_MAX_TITLE_LEN],
        "url": str(raw_url).strip()[:_PETAL_MAX_URL_LEN],
        "summary": summary[:_PETAL_MAX_SUMMARY_LEN],
    }


def _petal_format_answer_from_records(records: list[dict[str, str]]) -> str:
    lines: list[str] = []
    n = 0
    for rec in records:
        title = (rec.get("title") or "").strip()
        url = (rec.get("url") or "").strip()
        summary = (rec.get("summary") or "").strip()
        if not title and not url and not summary:
            continue
        n += 1
        display_title = title if title else "(无标题)"
        lines.append(f"{n}. {display_title}")
        if url:
            lines.append(f"   URL: {url}")
        if summary:
            lines.append(f"   Summary: {summary}")
    return "\n".join(lines)


def petal_search_sync(query: str, max_results: int, timeout_seconds: int) -> dict[str, Any]:
    search_url = _resolve_petal_search_url()
    header_map = _load_llm_default_headers()
    headers = {**header_map, "Content-Type": "application/json"}
    payload = {"query": query, "content": False}

    response = http_request(
        "POST",
        search_url,
        headers=headers,
        json=payload,
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    web_pages = data.get("web_pages", [])

    records: list[dict[str, str]] = []
    if isinstance(web_pages, list):
        for raw in web_pages[:max_results]:
            if not isinstance(raw, dict):
                continue
            records.append(_petal_normalize_web_page_item(raw))

    answer = _petal_format_answer_from_records(records)
    urls = [r["url"] for r in records if r.get("url")][:max_results]
    return {"provider": "petal", "answer": answer, "urls": urls}


def enable_petal_search() -> bool:
    return diagnose_petal_search() == "ok"


def diagnose_petal_search() -> str:
    """Return ``ok`` when Petal is usable, else a short reason for server logs."""
    try:
        if not bool(get_config().get("enable_petal_web_search", False)):
            return "enable_petal_web_search=false"
        _resolve_petal_search_url()
        _load_llm_default_headers()
        return "ok"
    except Exception as exc:
        return str(exc)


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
                maybe_url = (
                    item.get("url") or item.get("link") or item.get("source_url")
                )
                if maybe_url:
                    urls.append(str(maybe_url))
        if urls:
            return urls
    return []


def perplexity_search_sync(
    query: str, max_results: int, timeout_seconds: int
) -> dict[str, Any]:
    perplexity_key = os.environ.get("PERPLEXITY_API_KEY", "")
    if not perplexity_key:
        raise ValueError("PERPLEXITY_API_KEY is not set")

    payload = {
        "model": os.environ.get("PPLX_MODEL", "sonar-pro"),
        "messages": [
            {
                "role": "system",
                "content": "Provide concise answer and include citations.",
            },
            {"role": "user", "content": query},
        ],
        "max_tokens": 1024,
        "temperature": 0.2,
        "stream": False,
    }
    response = http_request(
        "POST",
        os.environ.get("PPLX_API_URL", "https://api.perplexity.ai/chat/completions"),
        headers={
            "Authorization": f"Bearer {perplexity_key}",
            "Content-Type": "application/json",
        },
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


def serper_search_sync(
    query: str, max_results: int, timeout_seconds: int
) -> dict[str, Any]:
    serper_key = os.environ.get("SERPER_API_KEY", "")
    if not serper_key:
        raise ValueError("SERPER_API_KEY is not set")

    response = http_request(
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


def jina_search_sync(query: str, timeout_seconds: int) -> dict[str, Any]:
    jina_key = os.environ.get("JINA_API_KEY", "")
    if not jina_key:
        raise ValueError("JINA_API_KEY is not set")

    payload = {
        "model": "jina-deepsearch-v1",
        "messages": [{"role": "user", "content": query}],
        "stream": False,
        "reasoning_effort": "low",
    }
    response = http_request(
        "POST",
        "https://deepsearch.jina.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {jina_key}",
            "Content-Type": "application/json",
        },
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


def bocha_search_sync(
    query: str, max_results: int, timeout_seconds: int
) -> dict[str, Any]:
    bocha_key = os.environ.get("BOCHA_API_KEY", "")
    if not bocha_key:
        raise ValueError("BOCHA_API_KEY is not set")

    response = http_request(
        "POST",
        os.environ.get("BOCHA_API_URL", "https://api.bocha.cn/v1/web-search"),
        headers={"Authorization": f"Bearer {bocha_key}", "Content-Type": "application/json"},
        json={"query": query, "summary": True, "count": max_results},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()

    urls: list[str] = []
    for container in (
        data.get("data", {}).get("webPages", {}).get("value"),
        data.get("webPages", {}).get("value"),
        data.get("data", {}).get("results"),
        data.get("results"),
    ):
        if not isinstance(container, list):
            continue
        for item in container[:max_results]:
            if isinstance(item, dict):
                maybe_url = item.get("url") or item.get("link")
                if maybe_url:
                    urls.append(str(maybe_url))
        if urls:
            break

    answer = ""
    for value in (
        data.get("summary"),
        data.get("answer"),
        data.get("data", {}).get("summary"),
        data.get("data", {}).get("answer"),
    ):
        if isinstance(value, str) and value.strip():
            answer = value.strip()
            break

    return {"provider": "bocha", "answer": answer, "urls": urls[:max_results]}


def _resolve_tavily_api_key() -> str:
    return os.environ.get("TAVILY_API_KEY", "").strip()


def _resolve_tavily_api_url() -> str:
    raw = (
        os.environ.get("TAVILY_API_URL", "").strip()
        or _TAVILY_DEFAULT_API_URL
    )
    return raw.rstrip("/")


def _tavily_format_answer_from_results(
    results: list[dict[str, Any]], summary_answer: str
) -> str:
    lines: list[str] = []
    summary = (summary_answer or "").strip()
    if summary:
        lines.append("Summary:")
        lines.append(summary)
        lines.append("")
    n = 0
    for item in results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        content = str(item.get("content") or "").strip()[:_TAVILY_MAX_CONTENT_LEN]
        if not title and not url and not content:
            continue
        n += 1
        display_title = title if title else "(无标题)"
        lines.append(f"{n}. {display_title}")
        if url:
            lines.append(f"   URL: {url}")
        if content:
            lines.append(f"   Content: {content}")
    return "\n".join(lines).strip()


def tavily_search_sync(
    query: str, max_results: int, timeout_seconds: int
) -> dict[str, Any]:
    api_key = _resolve_tavily_api_key()
    if not api_key:
        raise ValueError("TAVILY_API_KEY is not set")

    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "advanced",
        "include_answer": True,
    }
    response = http_request(
        "POST",
        f"{_resolve_tavily_api_url()}/search",
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=timeout_seconds,
    )
    payload.pop("api_key", None)
    response.raise_for_status()
    data = response.json()

    results_raw = data.get("results", [])
    results: list[dict[str, Any]] = []
    if isinstance(results_raw, list):
        for item in results_raw[:max_results]:
            if isinstance(item, dict):
                results.append(item)

    summary_answer = (data.get("answer") or "").strip()

    answer = _tavily_format_answer_from_results(results, summary_answer)
    urls: list[str] = []
    for item in results:
        maybe_url = item.get("url")
        if maybe_url:
            urls.append(str(maybe_url))
    return {"provider": "tavily", "answer": answer, "urls": urls[:max_results]}
