"""Exact actual-model route selection and receipt tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.common.e2a.constants import (
    E2A_INTERNAL_ACTUAL_MODEL_ROUTE_KEY,
    E2A_INTERNAL_EXPECTED_MODEL_ROUTE_KEY,
)
from jiuwenswarm.common.model_route import (
    ActualModelRouteReceipt,
    ExpectedModelRoute,
    build_model_route_index,
)
from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponseChunk
from jiuwenswarm.server.runtime.agent_adapter import interface_deep as deep_module
from jiuwenswarm.server.runtime.agent_adapter.interface import (
    _attach_actual_route_receipt_to_chunk,
)
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
)
from jiuwenswarm.integrations.ai4research_subscription.errors import (
    CodexProviderError,
)


def _entry(
    model_name: str,
    provider: str,
    *,
    alias: str = "",
    is_default: bool = False,
) -> dict[str, object]:
    return {
        "alias": alias,
        "is_default": is_default,
        "model_client_config": {
            "model_name": model_name,
            "client_provider": provider,
        },
    }


def test_shared_selector_preserves_alias_default_and_duplicate_indices() -> None:
    entries = [
        _entry("same", "ProviderA", alias="first"),
        _entry("other", "ProviderB", alias="shared", is_default=True),
        _entry("same", "ProviderC", alias="shared", is_default=True),
    ]
    index = build_model_route_index(entries)

    assert index.resolve("first").canonical_model_key == "same#0"
    assert index.resolve("shared").canonical_model_key == "other#0"
    assert index.resolve("same").canonical_model_key == "same#1"
    assert index.resolve("").canonical_model_key == "same#1"


def test_deep_adapter_construction_failure_falls_back_with_actual_receipt(
    monkeypatch,
) -> None:
    entries = [
        _entry("broken", "AI4ResearchCodex", alias="requested", is_default=True),
        _entry("working", "OpenAI"),
    ]
    monkeypatch.setattr(deep_module, "get_default_models", lambda _config: entries)

    def _build_model(mcc, _mco):
        if mcc["model_name"] == "broken":
            raise ValueError("construction failed")
        return SimpleNamespace(
            model_client_config=SimpleNamespace(
                client_provider=mcc["client_provider"],
            ),
            model_config=SimpleNamespace(model_name=mcc["model_name"]),
        )

    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_build_model_from_entry", _build_model)
    resolved_default = adapter._create_model({})
    request = AgentRequest(
        request_id="req-fallback",
        params={"model_name": "requested", "mode": "agent.fast"},
    )

    assert adapter._resolve_model_for_request(request) is resolved_default
    receipt = adapter._stamp_actual_model_route(request, resolved_default)
    assert receipt == ActualModelRouteReceipt(
        canonical_model_key="working#0",
        provider="OpenAI",
        source_request_id="req-fallback",
        mode="agent.fast",
    )
    assert request.metadata[E2A_INTERNAL_ACTUAL_MODEL_ROUTE_KEY] == receipt.to_dict()


def _build_adapter_with_models(
    monkeypatch,
    entries: list[dict[str, object]],
    *,
    broken_model_name: str | None = None,
) -> tuple[JiuWenSwarmDeepAdapter, list[SimpleNamespace]]:
    monkeypatch.setattr(deep_module, "get_default_models", lambda _config: entries)
    built: list[SimpleNamespace] = []

    def _build_model(mcc, _mco):
        if mcc["model_name"] == broken_model_name:
            raise ValueError("construction failed")
        model = SimpleNamespace(
            model_client_config=SimpleNamespace(
                client_provider=mcc["client_provider"],
            ),
            model_config=SimpleNamespace(model_name=mcc["model_name"]),
            invocation_count=0,
        )
        built.append(model)
        return model

    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_build_model_from_entry", _build_model)
    adapter._create_model({})
    return adapter, built


def _bound_request(
    *,
    model_key: str,
    provider: str,
    mode: str = "agent.fast",
    metadata: dict[str, object] | None = None,
) -> AgentRequest:
    expected = ExpectedModelRoute(
        canonical_model_key=model_key,
        provider=provider,
        mode=mode,
    )
    return AgentRequest(
        request_id="bound-answer",
        params={"model_name": model_key, "mode": mode},
        metadata=(
            {E2A_INTERNAL_EXPECTED_MODEL_ROUTE_KEY: expected.to_dict()}
            if metadata is None
            else metadata
        ),
        subscription_continuation_bound=True,
    )


@pytest.mark.parametrize(
    ("broken_provider", "fallback_provider"),
    [
        ("AI4ResearchCodex", "OpenAI"),
        ("OpenAI", "AI4ResearchCodex"),
    ],
)
def test_bound_continuation_never_falls_back_after_construction_failure(
    monkeypatch,
    broken_provider: str,
    fallback_provider: str,
) -> None:
    adapter, built = _build_adapter_with_models(
        monkeypatch,
        [
            _entry("broken", broken_provider, is_default=True),
            _entry("fallback", fallback_provider),
        ],
        broken_model_name="broken",
    )
    request = _bound_request(
        model_key="broken#0",
        provider=broken_provider,
    )

    with pytest.raises(CodexProviderError) as exc_info:
        adapter.preflight_subscription_request(request)

    assert exc_info.value.code == "route_unavailable"
    assert all(model.invocation_count == 0 for model in built)


def test_bound_continuation_rejects_provider_rebind_for_same_canonical_key(
    monkeypatch,
) -> None:
    adapter, built = _build_adapter_with_models(
        monkeypatch,
        [_entry("same", "OpenAI", is_default=True)],
    )
    request = _bound_request(
        model_key="same#0",
        provider="AI4ResearchCodex",
    )

    with pytest.raises(CodexProviderError) as exc_info:
        adapter.preflight_subscription_request(request)

    assert exc_info.value.code == "route_unavailable"
    assert all(model.invocation_count == 0 for model in built)


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {E2A_INTERNAL_EXPECTED_MODEL_ROUTE_KEY: {"canonical_model_key": "same#0"}},
        {
            E2A_INTERNAL_EXPECTED_MODEL_ROUTE_KEY: {
                "canonical_model_key": "other#0",
                "provider": "OpenAI",
                "mode": "agent.fast",
            }
        },
    ],
    ids=["missing", "malformed", "forged-mismatch"],
)
def test_bound_continuation_rejects_invalid_expected_route_before_invocation(
    monkeypatch,
    metadata: dict[str, object],
) -> None:
    adapter, built = _build_adapter_with_models(
        monkeypatch,
        [_entry("same", "OpenAI", is_default=True)],
    )
    request = _bound_request(
        model_key="same#0",
        provider="OpenAI",
        metadata=metadata,
    )

    with pytest.raises(CodexProviderError) as exc_info:
        adapter.preflight_subscription_request(request)

    assert exc_info.value.code == "route_unavailable"
    assert all(model.invocation_count == 0 for model in built)


def test_stream_facade_copies_actual_receipt_without_overwriting_chunk_metadata() -> None:
    receipt = ActualModelRouteReceipt(
        canonical_model_key="working#0",
        provider="OpenAI",
        source_request_id="req-stream",
        mode="agent.fast",
    )
    request = AgentRequest(
        request_id="req-stream",
        metadata={E2A_INTERNAL_ACTUAL_MODEL_ROUTE_KEY: receipt.to_dict()},
    )
    chunk = AgentResponseChunk(
        request_id="req-stream",
        channel_id="web",
        payload={"event_type": "chat.delta", "content": "x"},
        metadata={"fan_out_targets": ["one"]},
    )

    propagated = _attach_actual_route_receipt_to_chunk(request, chunk)

    assert propagated.metadata["fan_out_targets"] == ["one"]
    assert propagated.metadata[E2A_INTERNAL_ACTUAL_MODEL_ROUTE_KEY] == (
        receipt.to_dict()
    )
