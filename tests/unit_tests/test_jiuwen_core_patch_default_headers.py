from __future__ import annotations

from types import SimpleNamespace

import pytest
from openjiuwen.core.session.stream import OutputSchema

from jiuwenclaw.jiuwen_core_patch import (
    APIG_MODE_DEBUG_VALUE,
    APIG_MODE_HEADER,
    APIG_RATELIMIT_APP_HEADER,
    MAAS_APIG_METADATA_KEY,
    PatchOpenAIModelClient,
    RETRY_AFTER_HEADER,
    get_maas_apig_headers_for_call,
    _extract_maas_apig_headers,
    _inject_maas_apig_into_llm_usage_stream,
    _inject_span_id_kwargs,
    reset_maas_apig_headers_for_call,
    set_maas_apig_headers_for_call,
)

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


def test_inject_span_id_kwargs_adds_apig_debug_mode_and_preserves_headers():
    kwargs = {"custom_headers": {"Authorization": "Basic abc"}}

    out = _inject_span_id_kwargs(kwargs, "span-1")

    assert kwargs == {"custom_headers": {"Authorization": "Basic abc"}}
    assert out is not kwargs
    assert out["custom_headers"]["Authorization"] == "Basic abc"
    assert out["custom_headers"]["x-span-id"] == "span-1"
    assert out["custom_headers"][APIG_MODE_HEADER] == APIG_MODE_DEBUG_VALUE


def test_inject_span_id_kwargs_without_span_keeps_original_kwargs():
    kwargs = {"custom_headers": {"Authorization": "Basic abc"}}

    out = _inject_span_id_kwargs(kwargs, "")

    assert out is kwargs
    assert out == {"custom_headers": {"Authorization": "Basic abc"}}


def test_extract_maas_apig_headers_returns_raw_values_and_empty_defaults():
    assert _extract_maas_apig_headers({
        APIG_RATELIMIT_APP_HEADER: "remain:9,limit:10,time:10 second",
        RETRY_AFTER_HEADER: "30",
    }) == {
        "x_apig_ratelimit_app": "remain:9,limit:10,time:10 second",
        "retry_after": "30",
    }

    assert _extract_maas_apig_headers({"x-request-id": "req-1"}) == {
        "x_apig_ratelimit_app": "",
        "retry_after": "",
    }


def test_llm_usage_stream_payload_injects_maas_apig_metadata():
    token = set_maas_apig_headers_for_call({
        APIG_RATELIMIT_APP_HEADER: "remain:9,limit:10,time:10 second",
        RETRY_AFTER_HEADER: "",
    })
    try:
        data = OutputSchema(
            type="llm_usage",
            index=0,
            payload={
                "usage_metadata": {
                    "input_tokens": 123,
                    "output_tokens": 45,
                    "total_tokens": 168,
                },
                "result_type": "answer",
            },
        )

        out = _inject_maas_apig_into_llm_usage_stream(data)

        assert out.payload["usage_metadata"]["input_tokens"] == 123
        assert out.payload[MAAS_APIG_METADATA_KEY] == {
            "x_apig_ratelimit_app": "remain:9,limit:10,time:10 second",
            "retry_after": "",
        }
        assert get_maas_apig_headers_for_call() is None
    finally:
        reset_maas_apig_headers_for_call(token)
