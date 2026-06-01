# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from jiuwenclaw.agentserver.tools.web_search import (
    JiuwenHarnessWebSearchTool,
    ProviderRun,
    WebSearchRecord,
    build_web_search_tool_card,
    ensure_web_search_harness_metadata,
    evaluate_search_quality,
    format_web_search_response,
    normalize_search_mode,
    run_web_search,
)


def _rec(title: str, url: str, snippet: str = "", source: str = "test") -> WebSearchRecord:
    return WebSearchRecord(title=title, url=url, snippet=snippet, source=source)


def test_evaluate_search_quality_passes_with_two_snippets():
    records = [
        _rec("A", "https://a.com", "x" * 40),
        _rec("B", "https://b.com", "y" * 40),
    ]
    passed, reason = evaluate_search_quality(records, max_results=8)
    assert passed is True
    assert reason == "ok"


def test_evaluate_search_quality_paid_answer_with_one_citation():
    records = [_rec("Ref", "https://ref.com", "")]
    answer = "x" * 120
    passed, reason = evaluate_search_quality(
        records,
        answer=answer,
        max_results=8,
        skip_snippet_check=True,
    )
    assert passed is True
    assert reason == "answer_with_citations"


def test_evaluate_search_quality_paid_long_answer_without_skip_snippet():
    records = [_rec("Ref", "https://ref.com", "")]
    answer = "x" * 120
    passed, reason = evaluate_search_quality(records, answer=answer, max_results=8)
    assert passed is True
    assert reason == "answer_with_citations"


def test_normalize_search_mode_aliases():
    assert normalize_search_mode("") == "default"
    assert normalize_search_mode("paid") == "paid"
    assert normalize_search_mode("mcp_petal_search") == "paid"
    assert normalize_search_mode("free_search") == "free"


def test_format_web_search_response_header():
    text = format_web_search_response(
        "test query",
        search_mode="default",
        selected_provider="paid:petal",
        quality_passed=True,
        primary_records=[_rec("Main", "https://main.com", "snippet", "paid:petal")],
        supplementary_records=[],
        primary_answer="",
        providers_tried=["paid:petal(pass)"],
    )
    assert "search_mode=default" in text
    assert "selected=paid:petal" in text
    assert text.index("search_mode=") < text.index("Query:")


def test_build_web_search_tool_card():
    ensure_web_search_harness_metadata()
    card = build_web_search_tool_card(agent_id="main_agent", language="cn")
    assert card.name == "web_search"
    assert isinstance(card.input_params, dict)


def test_jiuwen_harness_web_search_tool_init():
    tool = JiuwenHarnessWebSearchTool(language="cn", agent_id="research_agent")
    assert tool.card.name == "web_search"


def test_run_web_search_paid_unavailable(monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.web_search.providers.any_paid_provider_available",
        lambda _order: False,
    )

    import asyncio

    result = asyncio.run(run_web_search("test", search_mode="paid"))
    assert result == "[ERROR]: paid search unavailable."


def test_run_web_search_free_does_not_call_paid(monkeypatch):
    paid_called = False

    async def fake_paid(*args, **kwargs):
        nonlocal paid_called
        paid_called = True
        return None, []

    async def fake_free(query, settings):
        return ProviderRun(
            provider="free:bing",
            records=[_rec("Hit", "https://hit.com", "x" * 40, "free:bing")],
            quality_passed=True,
        )

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.web_search.orchestrator.run_paid_chain",
        fake_paid,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.web_search.orchestrator.run_free_chain",
        fake_free,
    )

    import asyncio

    result = asyncio.run(run_web_search("test", search_mode="free"))
    assert paid_called is False
    assert "search_mode=free" in result
    assert "https://hit.com" in result


def test_run_web_search_default_paid_then_free(monkeypatch):
    async def fake_paid(query, settings):
        return None, ["paid:petal(skipped)"]

    async def fake_free(query, settings):
        return ProviderRun(
            provider="free:bing",
            records=[_rec("Hit", "https://hit.com", "x" * 40, "free:bing")],
            quality_passed=True,
        )

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.web_search.orchestrator.run_paid_chain",
        fake_paid,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.web_search.orchestrator.run_free_chain",
        fake_free,
    )

    import asyncio

    result = asyncio.run(run_web_search("test", search_mode="default"))
    assert "search_mode=default" in result
    assert "selected=free:bing" in result


def test_web_search_entry_delegates_to_orchestrator(monkeypatch):
    import asyncio

    calls = 0

    async def fake_run(query, *, search_mode="default", max_results=None):
        nonlocal calls
        calls += 1
        assert query == "hello"
        assert search_mode == "default"
        assert max_results is None
        return "ok"

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.web_search.tool.run_web_search",
        fake_run,
    )

    from jiuwenclaw.agentserver.tools.web_search.tool import web_search

    result = asyncio.run(web_search.invoke({"query": "hello"}))
    assert result == "ok"
    assert calls == 1
