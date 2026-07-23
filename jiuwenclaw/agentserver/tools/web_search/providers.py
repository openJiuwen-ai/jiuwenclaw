# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

from jiuwenclaw.agentserver.tools.web_search.free import (
    search_free_async,
    search_free_sync,
)
from jiuwenclaw.agentserver.tools.web_search.log_util import (
    paid_provider_skip_reason,
    provider_run_summary,
    truncate_query,
)
from jiuwenclaw.agentserver.tools.web_search.paid import (
    bocha_search_sync,
    enable_petal_search,
    jina_search_sync,
    perplexity_search_sync,
    petal_search_sync,
    serper_search_sync,
    tavily_search_sync,
)
from jiuwenclaw.agentserver.tools.web_search.constants import PAID_API_KEYS
from jiuwenclaw.agentserver.tools.web_search.quality import evaluate_search_quality
from jiuwenclaw.agentserver.tools.web_search.types import (
    ProviderRun,
    WebSearchRecord,
    WebSearchSettings,
)
from jiuwenclaw.local_env_config import read_env_if_set

logger = logging.getLogger(__name__)


def paid_provider_available(name: str) -> bool:
    if name == "petal":
        return enable_petal_search()
    env_key = PAID_API_KEYS.get(name)
    return bool(env_key and read_env_if_set(env_key))


def any_paid_provider_available(order: tuple[str, ...]) -> bool:
    return any(paid_provider_available(name) for name in order)


def _is_valid_http_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _title_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc or url
    return host.strip() or "(无标题)"


def records_from_free_rows(
    rows: list[dict[str, str]], engine: str, max_results: int
) -> list[WebSearchRecord]:
    source = f"free:{engine}"
    return [
        WebSearchRecord(
            title=(row.get("title") or "").strip() or "(无标题)",
            url=(row.get("url") or "").strip(),
            snippet=(row.get("snippet") or "").strip(),
            source=source,
        )
        for row in rows[:max_results]
    ]


def records_from_paid_payload(
    provider: str, payload: dict[str, Any], max_results: int
) -> tuple[list[WebSearchRecord], str]:
    answer = str(payload.get("answer", "") or "").strip()
    urls = [
        str(item).strip()
        for item in (payload.get("urls") or [])
        if item
    ][:max_results]
    records: list[WebSearchRecord] = []
    for url in urls:
        if not _is_valid_http_url(url):
            continue
        records.append(
            WebSearchRecord(
                title=_title_from_url(url),
                url=url,
                snippet="",
                source=f"paid:{provider}",
            )
        )
    return records, answer


async def invoke_paid_provider(
    name: str,
    query: str,
    max_results: int,
    timeout_seconds: int,
) -> ProviderRun:
    label = f"paid:{name}"
    if not paid_provider_available(name):
        logger.debug(
            "[web_search] paid skipped name=%s reason=%s",
            name,
            paid_provider_skip_reason(name),
        )
        return ProviderRun(provider=label, error="skipped")

    try:
        if name == "petal":
            payload = await asyncio.to_thread(
                petal_search_sync, query, max_results, timeout_seconds
            )
        elif name == "tavily":
            payload = await asyncio.to_thread(
                tavily_search_sync, query, max_results, timeout_seconds
            )
        elif name == "bocha":
            payload = await asyncio.to_thread(
                bocha_search_sync, query, max_results, timeout_seconds
            )
        elif name == "perplexity":
            payload = await asyncio.to_thread(
                perplexity_search_sync, query, max_results, timeout_seconds
            )
        elif name == "serper":
            payload = await asyncio.to_thread(
                serper_search_sync, query, max_results, timeout_seconds
            )
        elif name == "jina":
            payload = await asyncio.to_thread(
                jina_search_sync, query, timeout_seconds
            )
        else:
            logger.debug("[web_search] paid unknown provider name=%s", name)
            return ProviderRun(provider=label, error="unknown")
    except Exception as exc:
        logger.debug(
            "[web_search] paid error name=%s query=%r error=%s",
            name,
            truncate_query(query),
            exc,
            exc_info=True,
        )
        return ProviderRun(provider=label, error=str(exc))

    records, answer = records_from_paid_payload(name, payload, max_results)
    passed, reason = evaluate_search_quality(
        records,
        answer=answer,
        max_results=max_results,
        skip_snippet_check=True,
    )
    run = ProviderRun(
        provider=label,
        records=records,
        answer=answer,
        quality_passed=passed,
        quality_reason=reason,
    )
    logger.debug(
        "[web_search] paid finished name=%s %s",
        name,
        provider_run_summary(run),
    )
    return run


async def run_paid_chain(
    query: str,
    settings: WebSearchSettings,
    preferred_provider: str | None = None,
) -> tuple[ProviderRun | None, list[str]]:
    tried: list[str] = []
    last_run: ProviderRun | None = None
    order: tuple[str, ...] = settings.paid_provider_order
    if preferred_provider:
        preferred = preferred_provider.strip().lower()
        if preferred in order:
            order = (preferred,) + tuple(n for n in order if n != preferred)
        else:
            order = (preferred,) + order
    for name in order:
        run = await invoke_paid_provider(
            name,
            query,
            settings.max_results,
            settings.timeout_seconds,
        )
        last_run = run
        if run.error == "skipped":
            tried.append(f"{run.provider}(skipped)")
            continue
        if run.error:
            tried.append(f"{run.provider}(error)")
            continue
        status = "pass" if run.quality_passed else "low"
        tried.append(f"{run.provider}({status})")
        if run.quality_passed:
            return run, tried
    logger.debug(
        "[web_search] paid chain exhausted tried=%s last=%s",
        ", ".join(tried),
        provider_run_summary(last_run),
    )
    return last_run, tried


async def run_free_chain(query: str, settings: WebSearchSettings) -> ProviderRun:
    timeout = settings.timeout_seconds
    max_results = settings.max_results
    engine_used = ""
    try:
        engine_used, rows = await search_free_async(
            query, max_results, timeout, timeout
        )
    except Exception as exc:
        logger.debug(
            "[web_search] free async failed query=%r error=%s; sync fallback",
            truncate_query(query),
            exc,
            exc_info=True,
        )
        try:
            engine_used, rows = await asyncio.to_thread(
                search_free_sync, query, max_results, timeout
            )
        except Exception as fallback_exc:
            logger.debug(
                "[web_search] free sync failed query=%r error=%s",
                truncate_query(query),
                fallback_exc,
                exc_info=True,
            )
            return ProviderRun(provider="free", error=str(fallback_exc))

    records = records_from_free_rows(rows, engine_used, max_results)
    passed, reason = evaluate_search_quality(records, max_results=max_results)
    run = ProviderRun(
        provider=f"free:{engine_used}",
        records=records,
        quality_passed=passed,
        quality_reason=reason,
    )
    logger.debug(
        "[web_search] free finished engine=%s rows=%s %s",
        engine_used,
        len(rows),
        provider_run_summary(run),
    )
    return run
