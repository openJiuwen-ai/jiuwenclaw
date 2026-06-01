# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import logging

from jiuwenclaw.agentserver.tools.web_search.providers import (
    any_paid_provider_available,
    run_free_chain,
    run_paid_chain,
)
from jiuwenclaw.agentserver.tools.web_search.log_util import (
    paid_availability_report,
    provider_run_summary,
    settings_summary,
    truncate_query,
)
from jiuwenclaw.agentserver.tools.web_search.response import format_success_response
from jiuwenclaw.agentserver.tools.web_search.settings import resolve_web_search_settings
from jiuwenclaw.agentserver.tools.web_search.types import ProviderRun, WebSearchRecord

logger = logging.getLogger(__name__)

_SEARCH_MODE_ALIASES: dict[str, str] = {
    "": "default",
    "default": "default",
    "auto": "default",
    "paid": "paid",
    "paid_search": "paid",
    "mcp_paid_search": "paid",
    "petal": "paid",
    "petal_search": "paid",
    "mcp_petal_search": "paid",
    "free": "free",
    "free_search": "free",
    "mcp_free_search": "free",
}

_SUPPORTED_SEARCH_MODES = frozenset({"default", "paid", "free"})


def normalize_search_mode(value: str | None) -> str:
    key = (value or "").strip().lower()
    if not key:
        return "default"
    return _SEARCH_MODE_ALIASES.get(key, key)


def is_valid_search_mode(value: str | None) -> bool:
    return normalize_search_mode(value) in _SUPPORTED_SEARCH_MODES


def _has_usable_results(run: ProviderRun) -> bool:
    return bool(run.records or (run.answer or "").strip())


def _provider_status(run: ProviderRun) -> str:
    return "pass" if run.quality_passed else "low"


def _log_done(
    *,
    query: str,
    mode: str,
    provider: str,
    quality_passed: bool,
    providers_tried: list[str],
) -> None:
    logger.info(
        "[web_search] done query=%r search_mode=%s selected=%s quality=%s tried=%s",
        truncate_query(query),
        mode,
        provider,
        "pass" if quality_passed else "low",
        ", ".join(providers_tried),
    )


def _log_failed(
    *,
    query: str,
    mode: str,
    reason: str,
    providers_tried: list[str] | None = None,
    detail: str = "",
) -> None:
    tried = ", ".join(providers_tried) if providers_tried else "-"
    extra = f" detail={detail}" if detail else ""
    logger.warning(
        "[web_search] failed query=%r search_mode=%s reason=%s tried=%s%s",
        truncate_query(query),
        mode,
        reason,
        tried,
        extra,
    )


async def run_web_search(
    query: str,
    *,
    search_mode: str = "default",
    max_results: int | None = None,
) -> str:
    settings = resolve_web_search_settings(max_results)
    mode = normalize_search_mode(search_mode)
    logger.debug(
        "[web_search] start query=%r search_mode=%s %s",
        truncate_query(query),
        mode,
        settings_summary(settings),
    )

    if mode == "free":
        free_run = await run_free_chain(query, settings)
        if free_run.error:
            _log_failed(
                query=query,
                mode=mode,
                reason=free_run.error,
                detail=provider_run_summary(free_run),
            )
            return f"[ERROR]: free search failed: {free_run.error}"
        if not _has_usable_results(free_run):
            _log_failed(
                query=query,
                mode=mode,
                reason="no results",
                detail=provider_run_summary(free_run),
            )
            return "[ERROR]: free search failed: no results"
        tried = [f"{free_run.provider}({_provider_status(free_run)})"]
        _log_done(
            query=query,
            mode=mode,
            provider=free_run.provider,
            quality_passed=free_run.quality_passed,
            providers_tried=tried,
        )
        return format_success_response(
            query,
            mode,
            provider=free_run.provider,
            quality_passed=free_run.quality_passed,
            records=free_run.records,
            answer=free_run.answer,
            providers_tried=tried,
        )

    if mode == "paid":
        if not any_paid_provider_available(settings.paid_provider_order):
            _log_failed(
                query=query,
                mode=mode,
                reason="paid unavailable",
                detail=paid_availability_report(settings.paid_provider_order),
            )
            return "[ERROR]: paid search unavailable."
        paid_run, tried = await run_paid_chain(query, settings)
        if paid_run and paid_run.quality_passed:
            _log_done(
                query=query,
                mode=mode,
                provider=paid_run.provider,
                quality_passed=True,
                providers_tried=tried,
            )
            return format_success_response(
                query,
                mode,
                provider=paid_run.provider,
                quality_passed=True,
                records=paid_run.records,
                answer=paid_run.answer,
                providers_tried=tried,
            )
        _log_failed(
            query=query,
            mode=mode,
            reason="paid search failed",
            providers_tried=tried,
            detail=provider_run_summary(paid_run),
        )
        return "[ERROR]: paid search failed."

    paid_run, tried = await run_paid_chain(query, settings)
    if paid_run and paid_run.quality_passed:
        _log_done(
            query=query,
            mode=mode,
            provider=paid_run.provider,
            quality_passed=True,
            providers_tried=tried,
        )
        return format_success_response(
            query,
            mode,
            provider=paid_run.provider,
            quality_passed=True,
            records=paid_run.records,
            answer=paid_run.answer,
            providers_tried=tried,
        )

    earlier: list[WebSearchRecord] = []
    if paid_run and paid_run.records:
        earlier.extend(paid_run.records)

    logger.debug(
        "[web_search] default fallback free query=%r paid_tried=%s last_paid=%s",
        truncate_query(query),
        ", ".join(tried),
        provider_run_summary(paid_run),
    )
    free_run = await run_free_chain(query, settings)
    if free_run.error:
        _log_failed(
            query=query,
            mode=mode,
            reason=free_run.error,
            providers_tried=tried,
            detail=provider_run_summary(free_run),
        )
        return f"[ERROR]: free search failed: {free_run.error}"
    if not _has_usable_results(free_run):
        _log_failed(
            query=query,
            mode=mode,
            reason="no results",
            providers_tried=tried,
            detail=provider_run_summary(free_run),
        )
        return "[ERROR]: free search failed: no results"
    tried.append(f"{free_run.provider}({_provider_status(free_run)})")
    _log_done(
        query=query,
        mode=mode,
        provider=free_run.provider,
        quality_passed=free_run.quality_passed,
        providers_tried=tried,
    )
    return format_success_response(
        query,
        mode,
        provider=free_run.provider,
        quality_passed=free_run.quality_passed,
        records=free_run.records,
        answer=free_run.answer,
        supplementary_records=earlier,
        providers_tried=tried,
    )
