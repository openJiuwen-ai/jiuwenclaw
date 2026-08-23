from __future__ import annotations

# TEST ONLY: URL fixtures use reserved domains or security-test addresses; all
# request paths are stubbed or rejected before external network I/O.

from urllib.parse import quote

import pytest
import requests

from jiuwenswarm.agents.harness.common.tools import web_fetch_tools


class _Response:
    def __init__(
        self,
        url: str,
        *,
        status_code: int = 200,
        location: str = "",
        content: bytes = b"ok",
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.headers = {"Content-Type": "text/plain; charset=utf-8"}
        if location:
            self.headers["Location"] = location
        self.content = content
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status={self.status_code}")


def test_fetch_normalization_preserves_approved_ddg_redirect_url() -> None:
    target = "https://untrusted.example.test/path"
    approved = f"https://search.example.invalid/l/?uddg={quote(target)}"

    assert web_fetch_tools._normalize_url(approved) == approved
    assert web_fetch_tools._normalize_url("example.invalid/page") == (
        "https://example.invalid/page"
    )


def test_safe_fetch_validates_same_domain_redirect_hops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "https://example.invalid/start": _Response(
            "https://example.invalid/start",
            status_code=302,
            location="/middle",
        ),
        "https://example.invalid/middle": _Response(
            "https://example.invalid/middle",
            status_code=307,
            location="https://docs.example.invalid/end",
        ),
        "https://docs.example.invalid/end": _Response("https://docs.example.invalid/end"),
    }
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_get(url: str, **kwargs: object) -> _Response:
        calls.append((url, kwargs))
        return responses[url]

    monkeypatch.setattr(web_fetch_tools, "_http_get", fake_get)

    response = web_fetch_tools._safe_http_get(
        "https://example.invalid/start", timeout_seconds=9
    )

    assert response.url == "https://docs.example.invalid/end"
    assert [url for url, _kwargs in calls] == list(responses)
    assert all(kwargs["allow_redirects"] is False for _url, kwargs in calls)


def test_safe_fetch_rejects_cross_domain_redirect_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_get(url: str, **_kwargs: object) -> _Response:
        calls.append(url)
        return _Response(
            url,
            status_code=302,
            location="https://other.example.test/escape",
        )

    monkeypatch.setattr(web_fetch_tools, "_http_get", fake_get)

    with pytest.raises(ValueError, match="network_redirect_domain_mismatch"):
        web_fetch_tools._safe_http_get("https://example.invalid/start", timeout_seconds=9)

    assert calls == ["https://example.invalid/start"]


def test_safe_fetch_requests_ddg_url_before_rejecting_decoded_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = "https://untrusted.example.test/path"
    approved = f"https://search.example.invalid/l/?uddg={quote(target)}"
    calls: list[str] = []

    def fake_get(url: str, **_kwargs: object) -> _Response:
        calls.append(url)
        return _Response(url, status_code=302, location=target)

    monkeypatch.setattr(web_fetch_tools, "_http_get", fake_get)

    with pytest.raises(ValueError, match="network_redirect_domain_mismatch"):
        web_fetch_tools._safe_http_get(approved, timeout_seconds=9)

    assert calls == [approved]


def test_safe_fetch_rejects_redirect_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        "https://example.invalid/a": _Response(
            "https://example.invalid/a", status_code=302, location="/b"
        ),
        "https://example.invalid/b": _Response(
            "https://example.invalid/b", status_code=302, location="/a"
        ),
    }
    calls: list[str] = []

    def fake_get(url: str, **_kwargs: object) -> _Response:
        calls.append(url)
        return responses[url]

    monkeypatch.setattr(web_fetch_tools, "_http_get", fake_get)

    with pytest.raises(ValueError, match="network_redirect_loop"):
        web_fetch_tools._safe_http_get("https://example.invalid/a", timeout_seconds=9)

    assert calls == ["https://example.invalid/a", "https://example.invalid/b"]


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://example.invalid/plain", "network_scheme_not_https"),
        ("https://127.0.0.1/private", "network_host_not_public"),
        ("https://169.254.169.254/latest", "network_metadata_host"),
        ("https://metadata.google.internal/latest", "network_metadata_host"),
        ("https://service.internal/data", "network_internal_hostname"),
        ("https://user:pass@example.invalid/data", "network_url_userinfo"),
        ("https://example.invalid/data?access_token=secret", "network_secret_query"),
    ],
)
def test_safe_fetch_rejects_unsafe_initial_url_without_network(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    reason: str,
) -> None:
    monkeypatch.setattr(
        web_fetch_tools,
        "_http_get",
        lambda *_args, **_kwargs: pytest.fail("unsafe URL reached network"),
    )

    with pytest.raises(ValueError, match=reason):
        web_fetch_tools._safe_http_get(url, timeout_seconds=9)


def test_fetch_failure_does_not_invoke_implicit_jina_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_get(url: str, **_kwargs: object) -> _Response:
        calls.append(url)
        return _Response(url, status_code=403)

    monkeypatch.setattr(web_fetch_tools, "_http_get", fake_get)

    with pytest.raises(requests.HTTPError, match="status=403"):
        web_fetch_tools._fetch_webpage_sync(
            "https://example.invalid/protected", timeout_seconds=9
        )

    assert calls == ["https://example.invalid/protected"]


def test_proxy_retry_preserves_redirect_disable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct_calls: list[dict[str, object]] = []
    retry_calls: list[dict[str, object]] = []

    def direct_get(_url: str, **kwargs: object) -> _Response:
        direct_calls.append(kwargs)
        raise requests.exceptions.ProxyError("proxy failed")

    class Session:
        trust_env = True

        def __enter__(self) -> Session:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def get(self, url: str, **kwargs: object) -> _Response:
            retry_calls.append(kwargs)
            return _Response(url)

    monkeypatch.delenv("FREE_SEARCH_PROXY_URL", raising=False)
    monkeypatch.setattr(web_fetch_tools.requests, "get", direct_get)
    monkeypatch.setattr(web_fetch_tools.requests, "Session", Session)

    response = web_fetch_tools._http_get("https://example.invalid", allow_redirects=False)

    assert response.status_code == 200
    assert direct_calls[0]["allow_redirects"] is False
    assert retry_calls[0]["allow_redirects"] is False
