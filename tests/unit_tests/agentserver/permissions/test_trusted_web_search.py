# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the JiuwenSwarm-owned structured free-search producer."""

from __future__ import annotations

import pytest

from jiuwenswarm.agents.harness.common.rails.permissions.tool_invocation_key import (
    ToolInvocationKeyV1,
)
from jiuwenswarm.agents.harness.common.rails.permissions.trusted_search_urls import (
    SessionTrustedSearchUrls,
    bind_trusted_search_producer,
    clear_trusted_search_producer,
)
from jiuwenswarm.server.runtime.agent_adapter import trusted_web_search
from jiuwenswarm.server.runtime.agent_adapter.trusted_web_search import (
    TrustedWebFreeSearchTool,
)


def _key() -> ToolInvocationKeyV1:
    return ToolInvocationKeyV1(
        invocation_id="invoke-1",
        root_session_id="session-1",
        request_id="request-1",
        executor_kind="agent",
        execution_session_id="session-1",
        tool_call_id="call-1",
    )


@pytest.mark.asyncio
async def test_adapter_records_only_host_structured_search_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://news.example.test/typhoon"
    ledger = SessionTrustedSearchUrls("session-1")
    bind_trusted_search_producer(
        ledger=ledger,
        key=_key(),
        tool_name="free_search",
        max_results=8,
    )
    monkeypatch.setattr(
        trusted_web_search,
        "run_free_search_structured",
        lambda query, max_results, timeout_seconds: (
            "duckduckgo_html",
            ({"title": "Result", "url": url, "snippet": "Summary"},),
        ),
    )
    tool = TrustedWebFreeSearchTool(agent_id="agent-1")

    result = await tool.invoke({"query": "typhoon"})

    assert tool.card.name == "free_search"
    assert url in result
    assert ledger.contains(root_session_id="session-1", url=url)
    clear_trusted_search_producer()


@pytest.mark.asyncio
async def test_adapter_failure_records_no_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SessionTrustedSearchUrls("session-1")
    bind_trusted_search_producer(
        ledger=ledger,
        key=_key(),
        tool_name="free_search",
        max_results=8,
    )

    def fail(*args: object) -> object:
        raise RuntimeError("search unavailable")

    monkeypatch.setattr(trusted_web_search, "run_free_search_structured", fail)

    result = await TrustedWebFreeSearchTool(agent_id="agent-1").invoke(
        {"query": "typhoon"}
    )

    assert result.startswith("[ERROR]:")
    assert len(ledger) == 0
    clear_trusted_search_producer()


@pytest.mark.asyncio
async def test_adapter_keeps_provenance_when_rendering_fails_after_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://news.example.test/typhoon"
    ledger = SessionTrustedSearchUrls("session-1")
    bind_trusted_search_producer(
        ledger=ledger,
        key=_key(),
        tool_name="free_search",
        max_results=8,
    )
    monkeypatch.setattr(
        trusted_web_search,
        "run_free_search_structured",
        lambda query, max_results, timeout_seconds: (
            "duckduckgo_html",
            ({"title": "Result", "url": url, "snippet": "Summary"},),
        ),
    )

    def fail_render(**kwargs: object) -> str:
        raise RuntimeError("render failed")

    monkeypatch.setattr(trusted_web_search, "render_free_search_result", fail_render)

    with pytest.raises(RuntimeError, match="render failed"):
        await TrustedWebFreeSearchTool(agent_id="agent-1").invoke({"query": "typhoon"})

    assert ledger.contains(root_session_id="session-1", url=url)
    clear_trusted_search_producer()
