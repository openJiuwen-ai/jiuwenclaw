# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""JiuwenSwarm-owned structured free-search producer."""

from __future__ import annotations

import asyncio
from typing import Any

from openjiuwen.harness.tools import WebFreeSearchTool

from jiuwenswarm.agents.harness.common.rails.permissions.trusted_search_urls import (
    complete_trusted_search_producer,
)
from jiuwenswarm.agents.harness.common.tools.search_tools import (
    DEFAULT_SEARCH_MAX_RESULTS,
    normalize_search_max_results,
    render_free_search_result,
    run_free_search_structured,
)


class TrustedWebFreeSearchTool(WebFreeSearchTool):
    """Free search tool whose Host producer callback receives structured rows."""

    async def invoke(self, inputs: dict[str, Any], **kwargs: Any) -> str:
        del kwargs
        try:
            query = str(inputs.get("query", "") or "").strip()
            try:
                max_results = normalize_search_max_results(
                    int(
                        inputs.get("max_results", DEFAULT_SEARCH_MAX_RESULTS)
                        or DEFAULT_SEARCH_MAX_RESULTS
                    )
                )
                timeout_seconds = max(
                    5, min(int(inputs.get("timeout_seconds", 20) or 20), 60)
                )
            except (TypeError, ValueError):
                return "[ERROR]: invalid free search arguments."
            if not query:
                return "[ERROR]: query cannot be empty."
            try:
                engine_used, rows = await asyncio.to_thread(
                    run_free_search_structured,
                    query,
                    max_results,
                    timeout_seconds,
                )
            except Exception as exc:
                return f"[ERROR]: free search failed: {exc}"
            if not rows:
                return f"No search results for: {query}"
            urls = tuple(str(row.get("url", "") or "") for row in rows)
            complete_trusted_search_producer(
                tool_name="free_search",
                success=True,
                urls=urls,
            )
            return render_free_search_result(
                query=query,
                engine_used=engine_used,
                rows=rows,
            )
        finally:
            complete_trusted_search_producer(
                tool_name="free_search",
                success=False,
            )


__all__ = ["TrustedWebFreeSearchTool"]
