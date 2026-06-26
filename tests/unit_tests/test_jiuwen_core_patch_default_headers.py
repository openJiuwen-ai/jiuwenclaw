from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenclaw.jiuwen_core_patch import PatchOpenAIModelClient

HUAWEI_MAAS_SESSION_API_KEY = "huawei-maas-session"


@pytest.fixture
def patched_openai_client(monkeypatch):
    captured: dict = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
    return captured


def _make_client(api_key: str = "sk-real") -> PatchOpenAIModelClient:
    client = PatchOpenAIModelClient.__new__(PatchOpenAIModelClient)
    client.model_client_config = SimpleNamespace(
        api_base="https://api.openai.com/v1",
        api_key=api_key,
        verify_ssl=False,
        ssl_cert=None,
        timeout=30.0,
        max_retries=3,
    )
    return client


def test_invalid_default_headers_non_maas_still_creates_client(
    monkeypatch, patched_openai_client
):
    def _broken_headers():
        raise ValueError("default_headers is not valid JSON: Expecting value")

    monkeypatch.setattr(
        "jiuwenclaw.jiuwen_core_patch.read_default_headers",
        _broken_headers,
    )

    client = _make_client(api_key="sk-real")
    getattr(client, "_create_async_openai_client")()
    assert patched_openai_client.get("default_headers") is None


def test_invalid_default_headers_maas_degrades_to_none(
    monkeypatch, patched_openai_client
):
    def _broken_headers():
        raise ValueError("default_headers is not valid JSON: Expecting value")

    monkeypatch.setattr(
        "jiuwenclaw.jiuwen_core_patch.read_default_headers",
        _broken_headers,
    )

    client = _make_client(api_key=HUAWEI_MAAS_SESSION_API_KEY)
    getattr(client, "_create_async_openai_client")()
    assert patched_openai_client.get("default_headers") is None
