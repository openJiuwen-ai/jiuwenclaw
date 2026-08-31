# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""临时计费标记（billing_trace）单测：core 构造 / begin-middle 状态机 / 终态 trace /
开关 / TraceAwareModel 出口标记集成。"""

from __future__ import annotations

from dataclasses import asdict

import pytest

from jiuwenswarm.common.invocation_context import (
    INVOCATION_CONTEXT_VERSION,
    InvocationContext,
    TraceContext,
    reset_current_invocation_context,
    set_current_invocation_context,
)
from jiuwenswarm.common.invocation_context.billing_trace import (
    BEGIN_PREFIX,
    END_PREFIX,
    FAILED_PREFIX,
    MAX_CORE_LEN,
    MAX_TRACE_ID_LEN,
    TRACE_PREFIX,
    build_billing_core,
    has_begun,
    mark_model_call,
    reset_billing_trace_registry,
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
    def test_first_call_begin_then_middle(self) -> None:
        core = "sess-1&abc12345"
        assert mark_model_call(core) == f"{BEGIN_PREFIX}{core}"
        assert mark_model_call(core) == f"{TRACE_PREFIX}{core}"
        assert mark_model_call(core) == f"{TRACE_PREFIX}{core}"

    def test_cores_are_independent(self) -> None:
        assert mark_model_call("s1&i1").startswith(BEGIN_PREFIX)
        assert mark_model_call("s2&i2").startswith(BEGIN_PREFIX)
        assert mark_model_call("s1&i1").startswith(TRACE_PREFIX)
        assert not mark_model_call("s1&i1").startswith(BEGIN_PREFIX)

    def test_has_begun(self) -> None:
        assert not has_begun("sess-1&abc12345")
        mark_model_call("sess-1&abc12345")
        assert has_begun("sess-1&abc12345")

    def test_marker_disabled_returns_core_unchanged(self, monkeypatch) -> None:
        monkeypatch.setenv("JIUWEN_BILLING_TRACE_MARKER", "off")
        assert mark_model_call("sess-1&abc12345") == "sess-1&abc12345"
        assert not has_begun("sess-1&abc12345")


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
    """_with_trace_headers 出口集成：导出的裸 core 经 marker 加 begin/裸前缀。"""

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

    def test_first_call_begin_then_middle_prefix(self) -> None:
        token = set_current_invocation_context(self._invocation())
        try:
            first = self._trace_model()._with_trace_headers({})
            second = self._trace_model()._with_trace_headers({})
        finally:
            reset_current_invocation_context(token)

        assert first["custom_headers"]["x-hag-trace-id"] == (
            f"{BEGIN_PREFIX}root&19&abc&0"
        )
        assert second["custom_headers"]["x-hag-trace-id"] == (
            f"{TRACE_PREFIX}root&19&abc&0"
        )
        # 附属头不受标记影响
        assert first["custom_headers"]["x-session-id"] == "root"
        assert second["custom_headers"]["x-interaction-id"] == "19"

    def test_explicit_trace_header_wins_and_is_not_marked(self) -> None:
        token = set_current_invocation_context(self._invocation())
        try:
            kwargs = self._trace_model()._with_trace_headers(
                {"custom_headers": {"x-hag-trace-id": f"{END_PREFIX}root&19&abc&0"}}
            )
        finally:
            reset_current_invocation_context(token)

        assert kwargs["custom_headers"]["x-hag-trace-id"] == (
            f"{END_PREFIX}root&19&abc&0"
        )
        # 显式终态调用不占用 begin 名额（同一 core 的后续普通调用仍发 begin）
        assert not has_begun("root&19&abc&0")
