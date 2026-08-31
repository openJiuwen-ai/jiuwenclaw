# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""临时计费标记（billing_trace）单测：core 构造 / 首次登记状态机 / begin 与终态
trace 构造 / 虚拟标记调用派发 / 开关 / TraceAwareModel 出口标记集成。"""

from __future__ import annotations

import asyncio
from dataclasses import asdict

import pytest

from jiuwenswarm.common.invocation_context import (
    INVOCATION_CONTEXT_VERSION,
    InvocationContext,
    TraceContext,
    reset_current_invocation_context,
    set_current_invocation_context,
)
from jiuwenswarm.common.invocation_context import billing_trace
from jiuwenswarm.common.invocation_context.billing_trace import (
    BEGIN_PREFIX,
    END_PREFIX,
    FAILED_PREFIX,
    MAX_CORE_LEN,
    MAX_TRACE_ID_LEN,
    TRACE_PREFIX,
    begin_trace_id,
    build_billing_core,
    has_begun,
    mark_model_call,
    reset_billing_trace_registry,
    schedule_marker_call,
    terminal_trace_id,
)
from jiuwenswarm.common.invocation_context.model_trace import TraceAwareModel
from jiuwenswarm.server.xiaoyi_invocation import (
    XIAOYI_INVOCATION_EXTENSION_KEY,
    XiaoyiInvocationExtension,
    XiaoyiTraceHeaderExporter,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_billing_trace_registry()
    yield
    reset_billing_trace_registry()


class TestBuildBillingCore:
    def test_short_interaction_id_kept(self) -> None:
        assert build_billing_core("sess-1", "cron-run-1") == "sess-1&cron-run-1"

    def test_long_interaction_id_shortened_to_8(self) -> None:
        assert (
            build_billing_core("sess-1", "31675eb3-6199-4022-a58f-9ed63bf8f489")
            == "sess-1&31675eb3"
        )

    def test_typical_desktop_form_within_limit(self) -> None:
        core = build_billing_core(
            "desktop_1a03cf9f826_9d2ce4dcd3d1",
            "31675eb3-6199-4022-a58f-9ed63bf8f489",
        )
        assert core == "desktop_1a03cf9f826_9d2ce4dcd3d1&31675eb3"
        assert len(core) <= MAX_CORE_LEN

    def test_overlong_session_truncated_keeping_interaction(self) -> None:
        # 上限 45：先截 session 段，interaction 短码（8 位）完整保留
        core = build_billing_core("s" * 100, "31675eb3-6199-4022-a58f-9ed63bf8f489")
        assert core == f'{"s" * 36}&31675eb3'
        assert len(core) == 45

    def test_prefixed_forms_within_gateway_limit(self) -> None:
        # 最长前缀 failed-（19）+ core（45）= 64（celia 网关硬上限）
        assert len(FAILED_PREFIX) + MAX_CORE_LEN == MAX_TRACE_ID_LEN
        assert len(BEGIN_PREFIX) + MAX_CORE_LEN <= MAX_TRACE_ID_LEN


class TestMarkModelCall:
    def test_first_call_flagged_then_middle(self) -> None:
        core = "sess-1&abc12345"
        # 真实调用一律裸前缀；首次登记以 is_first=True 上报（调用方补发 begin 虚拟调用）
        assert mark_model_call(core) == (f"{TRACE_PREFIX}{core}", True)
        assert mark_model_call(core) == (f"{TRACE_PREFIX}{core}", False)
        assert mark_model_call(core) == (f"{TRACE_PREFIX}{core}", False)

    def test_cores_are_independent(self) -> None:
        assert mark_model_call("s1&i1")[1] is True
        assert mark_model_call("s2&i2")[1] is True
        assert mark_model_call("s1&i1") == (f"{TRACE_PREFIX}s1&i1", False)

    def test_has_begun(self) -> None:
        assert not has_begun("sess-1&abc12345")
        mark_model_call("sess-1&abc12345")
        assert has_begun("sess-1&abc12345")

    def test_marker_disabled_returns_core_unchanged(self, monkeypatch) -> None:
        monkeypatch.setenv("JIUWEN_BILLING_TRACE_MARKER", "off")
        assert mark_model_call("sess-1&abc12345") == ("sess-1&abc12345", False)
        assert not has_begun("sess-1&abc12345")


class TestBeginTraceId:
    def test_begin_form(self) -> None:
        assert begin_trace_id("sess-1&abc12345") == f"{BEGIN_PREFIX}sess-1&abc12345"

    def test_begin_within_gateway_limit(self) -> None:
        core = build_billing_core("s" * 100, "31675eb3-6199-4022-a58f-9ed63bf8f489")
        assert len(begin_trace_id(core)) <= MAX_TRACE_ID_LEN


class TestTerminalTraceId:
    def test_end_and_failed(self) -> None:
        core = "sess-1&abc12345"
        assert terminal_trace_id(core, ok=True) == f"{END_PREFIX}{core}"
        assert terminal_trace_id(core, ok=False) == f"{FAILED_PREFIX}{core}"

    def test_terminal_within_gateway_limit(self) -> None:
        core = build_billing_core("s" * 100, "31675eb3-6199-4022-a58f-9ed63bf8f489")
        assert len(terminal_trace_id(core, ok=False)) <= MAX_TRACE_ID_LEN
        assert len(terminal_trace_id(core, ok=True)) <= MAX_TRACE_ID_LEN


class TestTraceAwareModelMarking:
    """_with_trace_headers 出口集成：真实调用一律裸前缀，首次登记返回 begin core。"""

    def _invocation(self) -> InvocationContext:
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

    def _trace_model(self) -> TraceAwareModel:
        model = TraceAwareModel.__new__(TraceAwareModel)
        model._trace_header_exporters = (XiaoyiTraceHeaderExporter(),)
        return model

    def test_first_call_flagged_then_middle_prefix(self) -> None:
        token = set_current_invocation_context(self._invocation())
        try:
            first, first_begin = self._trace_model()._with_trace_headers({})
            second, second_begin = self._trace_model()._with_trace_headers({})
        finally:
            reset_current_invocation_context(token)

        assert first["custom_headers"]["x-hag-trace-id"] == (
            f"{TRACE_PREFIX}root&19&abc&0"
        )
        assert first_begin == "root&19&abc&0"
        assert second["custom_headers"]["x-hag-trace-id"] == (
            f"{TRACE_PREFIX}root&19&abc&0"
        )
        assert second_begin is None
        # 附属头不受标记影响
        assert first["custom_headers"]["x-session-id"] == "root"
        assert second["custom_headers"]["x-interaction-id"] == "19"

    def test_explicit_trace_header_wins_and_is_not_marked(self) -> None:
        token = set_current_invocation_context(self._invocation())
        try:
            kwargs, begin_core = self._trace_model()._with_trace_headers(
                {"custom_headers": {"x-hag-trace-id": f"{END_PREFIX}root&19&abc&0"}}
            )
        finally:
            reset_current_invocation_context(token)

        assert kwargs["custom_headers"]["x-hag-trace-id"] == (
            f"{END_PREFIX}root&19&abc&0"
        )
        assert begin_core is None
        # 显式标记调用不占用首次登记名额（同一 core 的后续普通调用仍上报 is_first）
        assert not has_begun("root&19&abc&0")


class _RecordingClient:
    """TraceAwareModel 底层 client 伪件：按序记录 invoke/stream 的 custom_headers。"""

    def __init__(self, fail_times: int = 0) -> None:
        self.calls: list[dict] = []
        self._fail_times = fail_times

    async def invoke(self, messages=None, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self._fail_times > 0:
            self._fail_times -= 1
            raise RuntimeError("boom")
        return "ok"

    async def stream(self, messages=None, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        yield "chunk"


def _recording_trace_model(client: _RecordingClient) -> TraceAwareModel:
    model = TraceAwareModel.__new__(TraceAwareModel)
    model._trace_header_exporters = (XiaoyiTraceHeaderExporter(),)
    model._client = client
    model.model_client_config = None
    model.model_config = None
    return model


def _call_traces(client: _RecordingClient) -> list[str | None]:
    return [
        (call.get("custom_headers") or {}).get("x-hag-trace-id")
        for call in client.calls
    ]


async def _drain_marker_tasks() -> None:
    tasks = list(billing_trace._MARKER_TASKS)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
class TestScheduleMarkerCall:
    async def test_marker_call_invokes_model_with_explicit_trace(self) -> None:
        client = _RecordingClient()
        model = _recording_trace_model(client)
        assert schedule_marker_call(model, f"{END_PREFIX}s&i") is True
        await _drain_marker_tasks()

        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["custom_headers"] == {"x-hag-trace-id": f"{END_PREFIX}s&i"}
        messages = call["messages"]
        assert [type(message).__name__ for message in messages] == [
            "SystemMessage",
            "UserMessage",
        ]
        assert all(
            getattr(message, "content", None) == "please only reply NO_REPLY"
            for message in messages
        )
        # 显式头透传：标记调用不占用该 core 的首次登记名额
        assert not has_begun("s&i")

    async def test_marker_call_retries_once_then_succeeds(self) -> None:
        client = _RecordingClient(fail_times=1)
        model = _recording_trace_model(client)
        assert schedule_marker_call(model, f"{BEGIN_PREFIX}s&i") is True
        await _drain_marker_tasks()
        assert len(client.calls) == 2

    async def test_marker_call_gives_up_after_retry(self) -> None:
        client = _RecordingClient(fail_times=99)
        model = _recording_trace_model(client)
        assert schedule_marker_call(model, f"{FAILED_PREFIX}s&i") is True
        await _drain_marker_tasks()
        assert len(client.calls) == 2


class TestScheduleMarkerCallDispatch:
    def test_dispatch_without_event_loop_returns_false(self) -> None:
        assert schedule_marker_call(object(), f"{END_PREFIX}s&i") is False


@pytest.mark.asyncio
class TestBeginMarkerOnFirstCall:
    """方案 A 集成：begin 是首呼前的独立 NO_REPLY 虚拟调用，真实调用一律裸前缀。"""

    def _invocation(self) -> InvocationContext:
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

    async def test_begin_virtual_call_precedes_first_real_call(self) -> None:
        client = _RecordingClient()
        model = _recording_trace_model(client)
        token = set_current_invocation_context(self._invocation())
        try:
            await model.invoke([{"role": "user", "content": "hi"}])
            await _drain_marker_tasks()
        finally:
            reset_current_invocation_context(token)

        assert _call_traces(client) == [
            f"{BEGIN_PREFIX}root&19&abc&0",
            f"{TRACE_PREFIX}root&19&abc&0",
        ]
        # begin 虚拟调用即 NO_REPLY 提示词形态
        begin_messages = client.calls[0]["messages"]
        assert all(
            getattr(message, "content", None) == "please only reply NO_REPLY"
            for message in begin_messages
        )

    async def test_subsequent_calls_do_not_repeat_begin(self) -> None:
        client = _RecordingClient()
        model = _recording_trace_model(client)
        token = set_current_invocation_context(self._invocation())
        try:
            await model.invoke([{"role": "user", "content": "hi"}])
            await model.invoke([{"role": "user", "content": "again"}])
            await _drain_marker_tasks()
        finally:
            reset_current_invocation_context(token)

        assert _call_traces(client) == [
            f"{BEGIN_PREFIX}root&19&abc&0",
            f"{TRACE_PREFIX}root&19&abc&0",
            f"{TRACE_PREFIX}root&19&abc&0",
        ]

    async def test_stream_path_also_fires_begin_first(self) -> None:
        client = _RecordingClient()
        model = _recording_trace_model(client)
        token = set_current_invocation_context(self._invocation())
        try:
            async for _ in model.stream([{"role": "user", "content": "hi"}]):
                pass
            await _drain_marker_tasks()
        finally:
            reset_current_invocation_context(token)

        assert _call_traces(client) == [
            f"{BEGIN_PREFIX}root&19&abc&0",
            f"{TRACE_PREFIX}root&19&abc&0",
        ]

    async def test_marker_disabled_sends_plain_core_without_begin(self, monkeypatch) -> None:
        monkeypatch.setenv("JIUWEN_BILLING_TRACE_MARKER", "off")
        client = _RecordingClient()
        model = _recording_trace_model(client)
        token = set_current_invocation_context(self._invocation())
        try:
            await model.invoke([{"role": "user", "content": "hi"}])
            await _drain_marker_tasks()
        finally:
            reset_current_invocation_context(token)

        assert _call_traces(client) == ["root&19&abc&0"]
