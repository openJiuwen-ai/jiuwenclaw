from __future__ import annotations

from dataclasses import asdict

from jiuwenswarm.common.invocation_context import (
    INVOCATION_CONTEXT_VERSION,
    InvocationContext,
    TraceContext,
    reset_current_invocation_context,
    set_current_invocation_context,
)
from jiuwenswarm.common.invocation_context.model_trace import (
    TraceAwareModel,
    export_trace_headers,
)
from jiuwenswarm.server.xiaoyi_invocation import (
    XIAOYI_INVOCATION_EXTENSION_KEY,
    XiaoyiInvocationExtension,
    XiaoyiTraceHeaderExporter,
)


def _invocation() -> InvocationContext:
    return InvocationContext(
        version=INVOCATION_CONTEXT_VERSION,
        invocation_id="invocation-1",
        request_id="request-1",
        session_id="session-1",
        channel_id="xiaoyi",
        chat_id="chat-1",
        metadata={
            XIAOYI_INVOCATION_EXTENSION_KEY: asdict(
                XiaoyiInvocationExtension(task_id="root&19&abc&0")
            )
        },
        trace=TraceContext(
            version=1,
            trace_id="root&19&abc&0",
            conversation_id="root",
            interaction_id="19",
        ),
    )


def _trace_model(*exporters) -> TraceAwareModel:
    model = TraceAwareModel.__new__(TraceAwareModel)
    model._trace_header_exporters = tuple(exporters)
    return model


def test_xiaoyi_exporter_emits_only_approved_trace_headers() -> None:
    assert export_trace_headers(_invocation(), (XiaoyiTraceHeaderExporter(),)) == {
        "x-hag-trace-id": "root&19&abc&0",
        "x-session-id": "root",
        "x-interaction-id": "19",
    }


def test_trace_aware_model_keeps_caller_explicit_trace_header() -> None:
    """调用方显式携带 x-hag-trace-id 时不覆盖、不改写（计费终态虚拟调用等；
    与 model-proxy hasTraceId 语义一致）。"""
    token = set_current_invocation_context(_invocation())
    try:
        kwargs = _trace_model(XiaoyiTraceHeaderExporter())._with_trace_headers(
            {
                "custom_headers": {
                    "x-hag-trace-id": "xiaoyi-work-end-root&19&abc&0",
                    "x-request-id": "keep",
                }
            }
        )
    finally:
        reset_current_invocation_context(token)

    assert kwargs["custom_headers"] == {
        "x-hag-trace-id": "xiaoyi-work-end-root&19&abc&0",
        "x-request-id": "keep",
    }


def test_trace_aware_model_uses_only_injected_exporters() -> None:
    class Exporter:
        def supports(self, _invocation) -> bool:
            return True

        def export(self, _trace) -> dict[str, str]:
            return {"x-example-trace": "trusted"}

    token = set_current_invocation_context(
        InvocationContext(
            version=INVOCATION_CONTEXT_VERSION,
            invocation_id="invocation-2",
            request_id="request-2",
            session_id="session-2",
            channel_id="web",
            chat_id="chat-2",
            trace=TraceContext(version=1, trace_id="trace-2"),
        )
    )
    try:
        kwargs = _trace_model(Exporter())._with_trace_headers(
            {"custom_headers": {"x-request-id": "keep"}}
        )
    finally:
        reset_current_invocation_context(token)

    assert kwargs["custom_headers"] == {
        "x-example-trace": "trusted",
        "x-request-id": "keep",
    }


def test_trace_exporter_does_nothing_without_matching_exporter() -> None:
    assert export_trace_headers(_invocation(), ()) == {}


def test_deep_adapter_builds_trace_aware_model() -> None:
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter

    model = JiuWenSwarmDeepAdapter._build_model_from_entry(
        {
            "model_name": "trace-test-model",
            "client_provider": "OpenAI",
            "api_base": "https://example.invalid/v1",
            "api_key": "test-key",
        },
        {},
    )

    assert isinstance(model, TraceAwareModel)
    assert model._trace_header_exporters
