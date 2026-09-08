# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the OpenJiuwen free-search provenance adapter."""

from __future__ import annotations

# TEST ONLY: URL fixtures use RFC-reserved ``.invalid`` names and no test
# performs external network I/O.

import re
from pathlib import Path
from typing import Any

import pytest
from openjiuwen.harness.tools import WebFreeSearchTool

from jiuwenswarm.agents.harness.common.rails.permissions.tool_invocation_key import (
    ToolInvocationKeyV1,
)
from jiuwenswarm.agents.harness.common.rails.permissions.trusted_search_urls import (
    SessionTrustedSearchUrls,
    bind_trusted_search_producer,
    clear_trusted_search_producer,
)
from jiuwenswarm.server.runtime.agent_adapter.trusted_web_search import (
    TrustedWebFreeSearchTool,
)


@pytest.fixture(autouse=True)
def _clear_producer_lease() -> None:
    clear_trusted_search_producer()
    yield
    clear_trusted_search_producer()


def _key() -> ToolInvocationKeyV1:
    return ToolInvocationKeyV1(
        invocation_id="invoke-1",
        root_session_id="session-1",
        request_id="request-1",
        executor_kind="agent",
        execution_session_id="session-1",
        tool_call_id="call-1",
    )


def _bind(ledger: SessionTrustedSearchUrls) -> None:
    bind_trusted_search_producer(
        ledger=ledger,
        key=_key(),
        tool_name="free_search",
        max_results=8,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inputs", "expected_max_results", "expected_timeout"),
    [
        ({"query": "release"}, 8, 20),
        ({"query": "release", "max_results": "8.0"}, 8, 20),
        ({"query": "release", "max_results": "invalid"}, 8, 20),
        ({"query": "release", "timeout_seconds": "invalid"}, 8, 20),
        ({"query": "release", "max_results": -3, "timeout_seconds": 1}, 1, 5),
        ({"query": "release", "max_results": 99, "timeout_seconds": 99}, 20, 60),
    ],
)
async def test_adapter_matches_upstream_output_and_argument_normalization(
    monkeypatch: pytest.MonkeyPatch,
    inputs: dict[str, Any],
    expected_max_results: int,
    expected_timeout: int,
) -> None:
    url = "https://release-notes.invalid/v1"
    calls: list[tuple[str, int, int]] = []

    async def fake_search(
        session: object, query: str, max_results: int, timeout_seconds: int
    ) -> tuple[str, list[dict[str, str]]]:
        del session
        calls.append((query, max_results, timeout_seconds))
        return (
            "duckduckgo",
            [
                {"title": "Release notes", "url": url, "snippet": "Summary"},
                {"title": "Additional notes", "url": "https://docs.invalid/v1"},
            ],
        )

    monkeypatch.setattr(WebFreeSearchTool, "_search_free", staticmethod(fake_search))
    upstream = WebFreeSearchTool(agent_id="agent-1")
    trusted = TrustedWebFreeSearchTool(agent_id="agent-1")
    expected = await upstream.invoke(inputs)

    ledger = SessionTrustedSearchUrls("session-1")
    _bind(ledger)
    actual = await trusted.invoke(inputs)

    assert actual == expected
    assert calls == [
        ("release", expected_max_results, expected_timeout),
        ("release", expected_max_results, expected_timeout),
    ]
    assert trusted.card.model_dump() == upstream.card.model_dump()
    assert ledger.contains(root_session_id="session-1", url=url)
    assert ledger.contains(root_session_id="session-1", url="https://docs.invalid/v1")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failure", "empty"])
async def test_adapter_matches_upstream_without_recording_unsuccessful_results(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    async def fake_search(
        session: object, query: str, max_results: int, timeout_seconds: int
    ) -> tuple[str, list[dict[str, str]]]:
        del session, query, max_results, timeout_seconds
        if outcome == "failure":
            raise RuntimeError("search unavailable")
        return "duckduckgo", []

    monkeypatch.setattr(WebFreeSearchTool, "_search_free", staticmethod(fake_search))
    expected = await WebFreeSearchTool(agent_id="agent-1").invoke({"query": "release"})

    ledger = SessionTrustedSearchUrls("session-1")
    _bind(ledger)
    actual = await TrustedWebFreeSearchTool(agent_id="agent-1").invoke(
        {"query": "release"}
    )

    assert actual == expected
    assert len(ledger) == 0


@pytest.mark.asyncio
async def test_adapter_matches_upstream_empty_query_without_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_search(*args: object) -> None:
        raise AssertionError(f"search should not run: {args!r}")

    monkeypatch.setattr(
        WebFreeSearchTool, "_search_free", staticmethod(unexpected_search)
    )
    inputs = {"query": "  ", "max_results": object()}
    expected = await WebFreeSearchTool(agent_id="agent-1").invoke(inputs)

    ledger = SessionTrustedSearchUrls("session-1")
    _bind(ledger)
    actual = await TrustedWebFreeSearchTool(agent_id="agent-1").invoke(inputs)

    assert actual == expected == "[ERROR]: query cannot be empty."
    assert len(ledger) == 0


@pytest.mark.asyncio
async def test_adapter_keeps_provenance_when_rendering_fails_after_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://release-notes.invalid/v1"

    async def fake_search(
        session: object, query: str, max_results: int, timeout_seconds: int
    ) -> tuple[str, list[dict[str, str]]]:
        del session, query, max_results, timeout_seconds
        return "duckduckgo", [{"url": url}]

    monkeypatch.setattr(WebFreeSearchTool, "_search_free", staticmethod(fake_search))
    ledger = SessionTrustedSearchUrls("session-1")
    _bind(ledger)

    with pytest.raises(KeyError, match="title"):
        await TrustedWebFreeSearchTool(agent_id="agent-1").invoke({"query": "release"})

    assert ledger.contains(root_session_id="session-1", url=url)


def test_adapter_owns_only_provenance_and_all_registrations_use_it() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    adapter_source = (
        repository_root
        / "jiuwenswarm/server/runtime/agent_adapter/trusted_web_search.py"
    ).read_text(encoding="utf-8")
    assert "run_free_search_structured" not in adapter_source

    for relative_path in (
        "jiuwenswarm/server/runtime/agent_adapter/interface_deep.py",
        "jiuwenswarm/server/runtime/agent_adapter/interface_code.py",
    ):
        source = (repository_root / relative_path).read_text(encoding="utf-8")
        assert "TrustedWebFreeSearchTool" in source
        assert re.search(r"(?<!Trusted)WebFreeSearchTool\(", source) is None
