# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from jiuwenclaw.agentserver.tools.web_search.content_cache import (
    CacheEntry,
    WebContentCache,
)


def _make_cache():
    return WebContentCache()


def _make_data(url="https://example.com/page", content="body text"):
    return {
        "url": url,
        "status_code": 200,
        "title": "Title",
        "content": content,
        "provider": "direct",
    }


def _first(result):
    assert isinstance(result, list)
    assert result, "expected at least one result item"
    return result[0]


@pytest.mark.asyncio
async def test_fetch_returns_cache_when_hit():
    from jiuwenclaw.agentserver.tools.web_fetch_tools import mcp_fetch_webpage

    cache = _make_cache()
    await cache.put(
        CacheEntry(
            url="https://example.com/page",
            content="cached body",
            update_time=1.0,
        )
    )

    with patch(
        "jiuwenclaw.agentserver.tools.web_fetch_tools._fetch_webpage_async",
        new=AsyncMock(),
    ) as mock_fetch:
        result = await mcp_fetch_webpage._func(
            url="https://example.com/page", cache=cache
        )
        assert mock_fetch.call_count == 0
    item = _first(result)
    assert item["from_cache"] is True
    assert "cached body" in item["content"]
    assert item.get("cache_age_days") is not None
    assert item.get("page_update_days") is not None


@pytest.mark.asyncio
async def test_fetch_falls_back_when_cache_miss():
    from jiuwenclaw.agentserver.tools.web_fetch_tools import mcp_fetch_webpage

    cache = _make_cache()
    data = _make_data()
    with patch(
        "jiuwenclaw.agentserver.tools.web_fetch_tools._fetch_webpage_async",
        new=AsyncMock(return_value=data),
    ) as mock_fetch:
        result = await mcp_fetch_webpage._func(
            url="https://example.com/page", cache=cache
        )
        assert mock_fetch.call_count == 1
    item = _first(result)
    assert item["from_cache"] is False
    assert "body text" in item["content"]


@pytest.mark.asyncio
async def test_use_cache_false_bypasses_cache():
    from jiuwenclaw.agentserver.tools.web_fetch_tools import mcp_fetch_webpage

    cache = _make_cache()
    await cache.put(CacheEntry(url="https://example.com/page", content="cached body"))

    data = _make_data(content="fresh body")
    with patch(
        "jiuwenclaw.agentserver.tools.web_fetch_tools._fetch_webpage_async",
        new=AsyncMock(return_value=data),
    ) as mock_fetch:
        result = await mcp_fetch_webpage._func(
            url="https://example.com/page", use_cache=False, cache=cache
        )
        assert mock_fetch.call_count == 1
    item = _first(result)
    assert item["from_cache"] is False
    assert "fresh body" in item["content"]


@pytest.mark.asyncio
async def test_fetch_no_cache_param_skips_cache_logic():
    from jiuwenclaw.agentserver.tools.web_fetch_tools import mcp_fetch_webpage

    data = _make_data()
    with patch(
        "jiuwenclaw.agentserver.tools.web_fetch_tools._fetch_webpage_async",
        new=AsyncMock(return_value=data),
    ) as mock_fetch:
        result = await mcp_fetch_webpage._func(url="https://example.com/page")
        assert mock_fetch.call_count == 1
    item = _first(result)
    assert item["from_cache"] is False


@pytest.mark.asyncio
async def test_fetch_result_is_dict_with_results_key():
    from jiuwenclaw.agentserver.tools.web_fetch_tools import mcp_fetch_webpage

    cache = _make_cache()
    data = _make_data()
    with patch(
        "jiuwenclaw.agentserver.tools.web_fetch_tools._fetch_webpage_async",
        new=AsyncMock(return_value=data),
    ):
        result = await mcp_fetch_webpage._func(
            url="https://example.com/page", cache=cache
        )
    assert isinstance(result, list)
    item = _first(result)
    assert "url" in item
    assert "status_code" in item
    assert "from_cache" in item


@pytest.mark.asyncio
async def test_fetch_multiple_urls_returns_one_item_per_url_in_order():
    from jiuwenclaw.agentserver.tools.web_fetch_tools import mcp_fetch_webpage

    urls = [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    ]

    def fake_fetch(url, timeout_seconds, overall_timeout):
        return _make_data(url=url, content=f"content for {url}")

    with patch(
        "jiuwenclaw.agentserver.tools.web_fetch_tools._fetch_webpage_async",
        new=AsyncMock(side_effect=fake_fetch),
    ) as mock_fetch:
        result = await mcp_fetch_webpage._func(url=urls)

    assert mock_fetch.call_count == 3
    assert isinstance(result, list)
    assert len(result) == 3
    for item, url in zip(result, urls):
        assert item["url"] == url
        assert url in item["content"]
        assert item["from_cache"] is False


@pytest.mark.asyncio
async def test_fetch_multiple_urls_partial_failure_keeps_error_item():
    from jiuwenclaw.agentserver.tools.web_fetch_tools import mcp_fetch_webpage

    good = "https://example.com/ok"
    bad = "https://example.com/bad"

    def fake_fetch(url, timeout_seconds, overall_timeout):
        if url == bad:
            raise RuntimeError("boom")
        return _make_data(url=url, content="ok content")

    with patch(
        "jiuwenclaw.agentserver.tools.web_fetch_tools._fetch_webpage_async",
        new=AsyncMock(side_effect=fake_fetch),
    ):
        result = await mcp_fetch_webpage._func(url=[good, bad])

    assert isinstance(result, list)
    assert len(result) == 2
    ok_item, bad_item = result
    assert ok_item["url"] == good
    assert "ok content" in ok_item["content"]
    assert "error" not in ok_item
    assert bad_item["url"] == bad
    assert bad_item.get("error")
    assert bad_item["content"] == ""


@pytest.mark.asyncio
async def test_fetch_empty_url_list_returns_empty_results():
    from jiuwenclaw.agentserver.tools.web_fetch_tools import mcp_fetch_webpage

    with patch(
        "jiuwenclaw.agentserver.tools.web_fetch_tools._fetch_webpage_async",
        new=AsyncMock(),
    ) as mock_fetch:
        result = await mcp_fetch_webpage._func(url=[])
    assert mock_fetch.call_count == 0
    assert result == []
