from contextlib import asynccontextmanager
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from jiuwenclaw.agentserver.deep_agent import interface_deep as interface_module
from jiuwenclaw.agentserver.deep_agent.deepresearch_rewrite_fast_path import (
    RewriteFastPathResult,
)
from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
from jiuwenclaw.schema.agent import AgentRequest


_PREPARED = {
    "status": "prepared",
    "context_token": "context-token",
    "action": "polish",
    "units": [
        {
            "unit_id": "unit_1",
            "slots": [{"slot_id": "slot_1", "text": "原句。"}],
        }
    ],
    "readonly_context": {"previous_unit": None, "next_unit": None},
    "instruction": "",
    "allowed_source_ids": [],
    "citation_evidence": [],
}
_STRUCTURED_RESULT = {
    "units": [
        {
            "unit_id": "unit_1",
            "slots": [{"slot_id": "slot_1", "text": "改写后的句子。"}],
        }
    ],
    "facts_added": False,
}
_COMPLETED = {"status": "completed", "report_delivered": True}


def _json_result(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _query() -> str:
    payload = {
        "report_path": "/workspace/report.md",
        "action": "polish",
        "selection": {
            "protocol_version": 2,
            "start_byte": 0,
            "end_byte": 9,
            "selected_text": "原句。",
            "source_sha256": "0" * 64,
        },
        "instruction": "",
    }
    return (
        "<deepresearch_rewrite_request>"
        f"{_json_result(payload)}"
        "</deepresearch_rewrite_request>"
    )


def _result(
    *,
    status: str = "completed",
    error_code: str | None = None,
    message: str = (
        "本轮改写已完成。若报告已是最终版本，请回复‘生成 HTML’；"
        "如需继续改写，可直接选择下一处内容。"
    ),
    usage_metadata: dict | None = None,
    model_calls: int = 1,
) -> RewriteFastPathResult:
    if usage_metadata is None and model_calls:
        usage_metadata = {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
        }
    return RewriteFastPathResult(
        recognized=True,
        status=status,
        action="polish",
        error_code=error_code,
        message=message,
        usage_metadata=usage_metadata,
        prepare_ms=1.0,
        model_ms=20.0,
        commit_ms=2.0,
        total_ms=23.0,
        model_calls=model_calls,
    )


@pytest.mark.asyncio
async def test_adapter_fast_path_ignores_plain_query_without_dependencies():
    adapter = object.__new__(JiuWenClawDeepAdapter)
    adapter._model = SimpleNamespace(invoke=AsyncMock())

    with patch(
        "jiuwenclaw.agentserver.tools.deepresearch.rewrite_tools."
        "deepresearch_prepare_rewrite._func",
        new=AsyncMock(),
    ) as prepare, patch(
        "jiuwenclaw.agentserver.tools.deepresearch.rewrite_tools."
        "deepresearch_commit_rewrite._func",
        new=AsyncMock(),
    ) as commit:
        result = await adapter._try_deepresearch_rewrite_fast_path("普通消息")

    assert result is None
    prepare.assert_not_awaited()
    adapter._model.invoke.assert_not_awaited()
    commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_adapter_fast_path_uses_request_model_and_existing_rewrite_tools():
    model_response = SimpleNamespace(
        content=_json_result(_STRUCTURED_RESULT),
        usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )
    adapter = object.__new__(JiuWenClawDeepAdapter)
    adapter._model = SimpleNamespace(invoke=AsyncMock(return_value=model_response))

    with patch(
        "jiuwenclaw.agentserver.tools.deepresearch.rewrite_tools."
        "deepresearch_prepare_rewrite._func",
        new=AsyncMock(return_value=_json_result(_PREPARED)),
    ) as prepare, patch(
        "jiuwenclaw.agentserver.tools.deepresearch.rewrite_tools."
        "deepresearch_commit_rewrite._func",
        new=AsyncMock(return_value=_json_result(_COMPLETED)),
    ) as commit:
        result = await adapter._try_deepresearch_rewrite_fast_path(_query())

    assert result is not None
    assert result.status == "completed"
    assert result.model_calls == 1
    assert result.usage_metadata == {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
    }
    prepare.assert_awaited_once()
    adapter._model.invoke.assert_awaited_once()
    commit.assert_awaited_once()


def test_adapter_fast_path_success_chunk_uses_fixed_invitation():
    chunks = JiuWenClawDeepAdapter._fast_path_chunks(
        _result(),
        request_id="request-1",
        channel_id="web",
    )

    assert len(chunks) == 1
    assert chunks[0].payload == {
        "event_type": "chat.final",
        "content": (
            "本轮改写已完成。若报告已是最终版本，请回复‘生成 HTML’；"
            "如需继续改写，可直接选择下一处内容。"
        ),
    }
    assert chunks[0].is_complete is False


def test_adapter_fast_path_error_chunk_contains_only_safe_error():
    chunks = JiuWenClawDeepAdapter._fast_path_chunks(
        _result(
            status="error",
            error_code="MODEL_OUTPUT_INVALID",
            message="invalid structured rewrite result",
        ),
        request_id="request-1",
        channel_id="web",
    )

    assert len(chunks) == 1
    assert chunks[0].payload == {
        "event_type": "chat.final",
        "content": "改写失败（MODEL_OUTPUT_INVALID）：invalid structured rewrite result",
    }
    assert "原句" not in chunks[0].payload["content"]
    assert chunks[0].is_complete is False


def _stream_adapter(fast_path_result: RewriteFastPathResult | None):
    adapter = object.__new__(JiuWenClawDeepAdapter)
    adapter._instance = SimpleNamespace()
    adapter._model = SimpleNamespace(
        model_config=SimpleNamespace(model_name="test-model"),
    )
    adapter._stream_event_rail = None
    adapter._telemetry_rail = None
    adapter._request_summary_rail = None
    adapter._skill_evolution_rail = None
    adapter._try_skill_turbo_resume = AsyncMock(return_value=None)
    adapter._has_valid_model_config = Mock(return_value=True)
    adapter._on_chat_request_start = AsyncMock(return_value=(None, None, None))
    adapter._on_chat_request_end = AsyncMock()
    adapter._plain_chat_should_clear_stale_interrupt = Mock(return_value=False)
    adapter._handle_slash_command = AsyncMock(return_value=None)
    adapter._bind_runtime_cron_context = Mock(return_value=())
    adapter._reset_runtime_cron_context = Mock()
    adapter._tenant_disk_ids = Mock(return_value=("default", "default"))
    adapter._update_runtime_config = AsyncMock()
    adapter._try_deepresearch_rewrite_fast_path = AsyncMock(
        return_value=fast_path_result
    )
    adapter._untrack_session_toolkit = Mock()
    adapter._cleanup_circuit_breaker_session = Mock()
    adapter._schedule_background_evolution_followup = Mock()
    return adapter


@asynccontextmanager
async def _request_scope(**_kwargs):
    yield


async def _collect_stream(adapter, query: str):
    request = AgentRequest(
        request_id="request-1",
        channel_id="web",
        session_id="session-1",
        params={"query": query, "mode": "agent.plan"},
        metadata={},
        is_stream=True,
    )
    chunks = []
    async for chunk in adapter.process_message_stream_impl(
        request,
        {"query": query, "conversation_id": "session-1"},
    ):
        chunks.append(chunk)
    return chunks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fast_path_result",
    [
        _result(),
        _result(
            status="error",
            error_code="REVISION_CONFLICT",
            message="the report revision changed",
            usage_metadata=None,
            model_calls=0,
        ),
    ],
)
async def test_process_stream_skips_runner_for_recognized_fast_path(
    monkeypatch,
    fast_path_result,
):
    adapter = _stream_adapter(fast_path_result)
    runner_calls = []

    async def empty_runner(*_args, **_kwargs):
        runner_calls.append("runner")
        if False:
            yield None

    monkeypatch.setattr(
        interface_module.Runner,
        "run_agent_streaming",
        empty_runner,
    )
    monkeypatch.setattr(
        interface_module,
        "ask_user_question_request_scope",
        _request_scope,
    )
    monkeypatch.setattr(interface_module, "setup_permission_context", Mock())
    monkeypatch.setattr(interface_module, "cleanup_permission_context", Mock())
    monkeypatch.setattr(interface_module, "set_perf_summary_context", Mock())
    monkeypatch.setattr(interface_module, "finalize_perf_summary_request", Mock())
    monkeypatch.setattr(interface_module, "clear_perf_summary_context", Mock())
    monkeypatch.setattr(interface_module, "mark_request_first_byte", Mock())

    chunks = await _collect_stream(adapter, _query())

    assert runner_calls == []
    event_types = [chunk.payload["event_type"] for chunk in chunks if chunk.payload]
    assert event_types[0] == "chat.final"
    assert ("chat.usage_summary" in event_types) is bool(
        fast_path_result.usage_metadata
    )
    assert sum(chunk.is_complete for chunk in chunks) == 1


@pytest.mark.asyncio
async def test_process_stream_keeps_runner_for_plain_message(monkeypatch):
    adapter = _stream_adapter(None)
    runner_calls = []

    async def empty_runner(*_args, **_kwargs):
        runner_calls.append("runner")
        if False:
            yield None

    monkeypatch.setattr(
        interface_module.Runner,
        "run_agent_streaming",
        empty_runner,
    )
    monkeypatch.setattr(
        interface_module,
        "ask_user_question_request_scope",
        _request_scope,
    )
    monkeypatch.setattr(interface_module, "setup_permission_context", Mock())
    monkeypatch.setattr(interface_module, "cleanup_permission_context", Mock())
    monkeypatch.setattr(interface_module, "set_perf_summary_context", Mock())
    monkeypatch.setattr(interface_module, "finalize_perf_summary_request", Mock())
    monkeypatch.setattr(interface_module, "clear_perf_summary_context", Mock())
    monkeypatch.setattr(interface_module, "mark_request_first_byte", Mock())

    chunks = await _collect_stream(adapter, "普通消息")

    assert runner_calls == ["runner"]
    assert sum(chunk.is_complete for chunk in chunks) == 1
