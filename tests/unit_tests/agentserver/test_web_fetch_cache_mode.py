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
    assert "FromCache: true" in result
    assert "cached body" in result
    assert "CacheAgeDays:" in result
    assert "PageUpdateDays:" in result
    assert "use_cache=false" in result


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
    assert "FromCache: false" in result
    assert "body text" in result


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
    assert "FromCache: false" in result
    assert "fresh body" in result


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
    assert "FromCache: false" in result


@pytest.mark.asyncio
async def test_fetch_result_format_includes_from_cache_flag():
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
    assert "FromCache:" in result
    assert "URL:" in result
    assert "Status:" in result
