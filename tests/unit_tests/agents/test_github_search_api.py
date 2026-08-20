# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.tools.web_fetch_tools import (
    _github_search_api_url,
    mcp_fetch_webpage,
)


def _run(coro) -> str:
    return asyncio.run(coro)


def _call(url: str, max_chars: int = 0, timeout_seconds: int = 30) -> str:
    """调用 @tool 包装后的底层 async 函数(mcp_fetch_webpage._func)。"""
    return _run(mcp_fetch_webpage._func(url, max_chars=max_chars, timeout_seconds=timeout_seconds))


def _fake_response(
    body: bytes,
    *,
    status_code: int = 200,
    content_type: str = "text/html",
    url: str = "https://example.com/",
) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status_code,
        headers={"Content-Type": content_type},
        content=body,
        encoding="utf-8",
        apparent_encoding="utf-8",
        url=url,
        raise_for_status=lambda: None,
    )


# ── _github_search_api_url ─────────────────────────────────────────


class TestGithubSearchApiUrl:
    @pytest.mark.parametrize(
        "url, expected",
        [
            (
                "https://github.com/search?q=deepseek+harness&type=repositories",
                "https://api.github.com/search/repositories?q=deepseek%20harness&per_page=10",
            ),
            (
                "https://github.com/search?q=deepseek+harness",
                "https://api.github.com/search/repositories?q=deepseek%20harness&per_page=10",
            ),
            (
                "https://www.github.com/search?q=foo&type=repositories&per_page=5",
                "https://api.github.com/search/repositories?q=foo&per_page=5",
            ),
            # 中文/特殊字符必须 URL 编码
            (
                "https://github.com/search?q=量化+因子&type=repositories",
                "https://api.github.com/search/repositories?q=%E9%87%8F%E5%8C%96%20%E5%9B%A0%E5%AD%90&per_page=10",
            ),
        ],
    )
    def test_rewrites_repository_search(self, url: str, expected: str) -> None:
        assert _github_search_api_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/search?q=foo&type=issues",
            "https://github.com/search?q=foo&type=pullrequests",
            "https://github.com/search",  # 缺 q
            "https://example.com/search?q=foo&type=repositories",
            "https://github.com/notsearch?q=foo",
            "https://api.github.com/search/repositories?q=foo",
        ],
    )
    def test_does_not_rewrite_other_urls(self, url: str) -> None:
        assert _github_search_api_url(url) is None

    def test_per_page_clamped_to_30(self) -> None:
        url = "https://github.com/search?q=foo&type=repositories&per_page=999"
        assert _github_search_api_url(url) == (
            "https://api.github.com/search/repositories?q=foo&per_page=30"
        )


# ── mcp_fetch_webpage: GitHub search 重写生效 ─────────────────────


class TestFetchWebpageGithubRewrite:
    def test_github_search_goes_to_api(self, monkeypatch) -> None:
        captured: list[str] = []

        def fake_get(url, **kwargs) -> SimpleNamespace:
            captured.append(url)
            return _fake_response(
                b'{"total_count": 1, "items": [{"full_name": "a/b"}]}',
                content_type="application/json",
            )

        monkeypatch.setattr("jiuwenswarm.agents.harness.common.tools.web_fetch_tools._http_get", fake_get)
        result = _call("https://github.com/search?q=deepseek+harness&type=repositories")
        assert captured == ["https://api.github.com/search/repositories?q=deepseek%20harness&per_page=10"]
        assert "a/b" in result
