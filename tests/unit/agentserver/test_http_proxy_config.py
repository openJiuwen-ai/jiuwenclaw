# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import os

import pytest

from jiuwenclaw.http_proxy_config import (
    read_proxy_url,
    resolve_requests_proxies,
    requests_get,
    should_bypass_proxy,
)
import jiuwenclaw.http_proxy_config as proxy_cfg
from jiuwenclaw.local_env_config import (
    bind_task_env_overlay,
    reset_local_env_state_for_tests,
    reset_task_env_overlay,
    set_os_environ,
)


@pytest.fixture(autouse=True)
def _reset_env_state():
    saved_environ = dict(os.environ)
    reset_local_env_state_for_tests()
    yield
    reset_local_env_state_for_tests()
    os.environ.clear()
    os.environ.update(saved_environ)


@pytest.fixture(autouse=True)
def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "default__default__HTTP_PROXY",
        "default__default__HTTPS_PROXY",
        "default__default__NO_PROXY",
    ):
        monkeypatch.delenv(key, raising=False)


def test_read_proxy_url_prefers_overlay() -> None:
    token = bind_task_env_overlay({"HTTPS_PROXY": "http://overlay:3128"})
    try:
        assert read_proxy_url() == "http://overlay:3128"
    finally:
        reset_task_env_overlay(token)


def test_read_proxy_url_reads_namespaced_os_environ() -> None:
    set_os_environ(
        "HTTP_PROXY",
        "http://tenant:8080",
        service_id="default",
        agent_id="default",
    )
    assert read_proxy_url() == "http://tenant:8080"


def test_should_bypass_proxy_honors_overlay_no_proxy() -> None:
    token = bind_task_env_overlay(
        {
            "HTTPS_PROXY": "http://proxy:8080",
            "NO_PROXY": "example.com",
        }
    )
    try:
        assert should_bypass_proxy("https://example.com/path") is True
        assert should_bypass_proxy("https://other.com/path") is False
        assert resolve_requests_proxies("https://other.com") == {
            "http": "http://proxy:8080",
            "https": "http://proxy:8080",
        }
        assert resolve_requests_proxies("https://example.com") is None
    finally:
        reset_task_env_overlay(token)


def test_requests_request_ignores_bare_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://bare:9999")

    calls: list[dict] = []

    class _FakeSession:
        trust_env = True

        def request(self, method: str, url: str, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            class _Resp:
                status_code = 200

            return _Resp()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(proxy_cfg.requests, "Session", lambda: _FakeSession())

    token = bind_task_env_overlay({"HTTPS_PROXY": "http://overlay:3128"})
    try:
        requests_get("https://api.example.com/v1")
    finally:
        reset_task_env_overlay(token)

    assert calls
    assert calls[0]["proxies"] == {
        "http": "http://overlay:3128",
        "https": "http://overlay:3128",
    }
