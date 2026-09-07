# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for reasoning-only / silent-complete visible-reply fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

from jiuwenswarm.common.chat_final import (
    fill_reasoning_only_empty_final_content,
    fill_silent_complete_visible_reply,
    reasoning_only_empty_reply_fallback_text,
)
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


def test_reasoning_only_empty_reply_fallback_text_zh_and_en() -> None:
    zh = reasoning_only_empty_reply_fallback_text("zh")
    en = reasoning_only_empty_reply_fallback_text("en")
    assert zh.startswith("本轮未生成可见回复")
    assert en.startswith("No visible reply was generated this turn")


def test_fill_reasoning_only_empty_final_content_gates() -> None:
    fallback = reasoning_only_empty_reply_fallback_text("zh")

    assert (
        fill_reasoning_only_empty_final_content(
            content="",
            has_visible_streamed_text=False,
            has_reasoning=True,
            lang="zh",
        )
        == fallback
    )
    assert (
        fill_reasoning_only_empty_final_content(
            content="",
            has_visible_streamed_text=True,
            has_reasoning=True,
            lang="zh",
        )
        == ""
    )
    assert (
        fill_reasoning_only_empty_final_content(
            content="",
            has_visible_streamed_text=False,
            has_reasoning=False,
            lang="zh",
        )
        == ""
    )
    assert (
        fill_reasoning_only_empty_final_content(
            content="已添加待办",
            has_visible_streamed_text=False,
            has_reasoning=True,
            lang="zh",
        )
        == "已添加待办"
    )


def test_fill_silent_complete_visible_reply_no_reasoning_required() -> None:
    fallback = reasoning_only_empty_reply_fallback_text("zh")
    assert (
        fill_silent_complete_visible_reply(
            content="",
            has_visible_streamed_text=False,
            lang="zh",
        )
        == fallback
    )
    assert (
        fill_silent_complete_visible_reply(
            content="",
            has_visible_streamed_text=True,
            lang="zh",
        )
        == ""
    )
    assert (
        fill_silent_complete_visible_reply(
            content="已完成",
            has_visible_streamed_text=False,
            lang="zh",
        )
        == "已完成"
    )


def test_deep_adapter_apply_preserves_dedicated_fallback() -> None:
    assert (
        JiuWenSwarmDeepAdapter._apply_reasoning_only_empty_reply_fallback(
            has_streamed_content=False,
            had_reasoning_output=True,
            fallback_content="Plan approved.",
            reasoning_only_fallback="FALLBACK",
        )
        == "Plan approved."
    )
    assert (
        JiuWenSwarmDeepAdapter._apply_reasoning_only_empty_reply_fallback(
            has_streamed_content=False,
            had_reasoning_output=True,
            fallback_content="",
            reasoning_only_fallback="FALLBACK",
        )
        == "FALLBACK"
    )
    assert (
        JiuWenSwarmDeepAdapter._apply_reasoning_only_empty_reply_fallback(
            has_streamed_content=True,
            had_reasoning_output=True,
            fallback_content="",
            reasoning_only_fallback="FALLBACK",
        )
        == ""
    )


def test_deep_adapter_silent_complete_fill_without_reasoning() -> None:
    assert (
        JiuWenSwarmDeepAdapter._apply_silent_complete_visible_reply(
            has_streamed_content=False,
            fallback_content="",
            silent_fallback="FALLBACK",
        )
        == "FALLBACK"
    )
    assert (
        JiuWenSwarmDeepAdapter._apply_silent_complete_visible_reply(
            has_streamed_content=False,
            fallback_content="Plan approved.",
            silent_fallback="FALLBACK",
        )
        == "Plan approved."
    )
    assert (
        JiuWenSwarmDeepAdapter._apply_silent_complete_visible_reply(
            has_streamed_content=True,
            fallback_content="",
            silent_fallback="FALLBACK",
        )
        == ""
    )


def test_should_emit_silent_visible_reply_gates() -> None:
    assert (
        JiuWenSwarmDeepAdapter._should_emit_silent_visible_reply(
            stream_is_user_originated=True,
            had_visible_text_ever=False,
            hitl_pending_stream=False,
        )
        is True
    )
    assert (
        JiuWenSwarmDeepAdapter._should_emit_silent_visible_reply(
            stream_is_user_originated=False,
            had_visible_text_ever=False,
            hitl_pending_stream=False,
        )
        is False
    )
    assert (
        JiuWenSwarmDeepAdapter._should_emit_silent_visible_reply(
            stream_is_user_originated=True,
            had_visible_text_ever=True,
            hitl_pending_stream=False,
        )
        is False
    )
    assert (
        JiuWenSwarmDeepAdapter._should_emit_silent_visible_reply(
            stream_is_user_originated=True,
            had_visible_text_ever=False,
            hitl_pending_stream=True,
        )
        is False
    )
    assert (
        JiuWenSwarmDeepAdapter._should_emit_silent_visible_reply(
            stream_is_user_originated=True,
            had_visible_text_ever=False,
            hitl_pending_stream=False,
            run_failure=("task_failed", "boom"),
        )
        is False
    )
    assert (
        JiuWenSwarmDeepAdapter._should_emit_silent_visible_reply(
            stream_is_user_originated=True,
            had_visible_text_ever=False,
            hitl_pending_stream=False,
            emitted_chat_error=True,
        )
        is False
    )


def test_facade_silent_complete_skips_named_deferred_terminal() -> None:
    from jiuwenswarm.common.schema.agent import AgentResponseChunk
    from jiuwenswarm.server.runtime.agent_adapter.interface import (
        _deferred_terminal_has_event_type,
        _should_facade_silent_complete_rescue,
    )

    named = AgentResponseChunk(
        request_id="r1",
        channel_id="web",
        payload={"event_type": "chat.done"},
        is_complete=True,
    )
    blank = AgentResponseChunk(
        request_id="r1",
        channel_id="web",
        payload=None,
        is_complete=True,
    )
    assert _deferred_terminal_has_event_type(named) is True
    assert _deferred_terminal_has_event_type(blank) is False
    assert (
        _should_facade_silent_complete_rescue(
            is_team_mode=False,
            suppress_a2ui_stream=False,
            saw_invocation_paused=False,
            has_facade_visible_text=False,
            request_params={},
            deferred_terminal_has_event=True,
        )
        is False
    )
    assert (
        _should_facade_silent_complete_rescue(
            is_team_mode=False,
            suppress_a2ui_stream=False,
            saw_invocation_paused=False,
            has_facade_visible_text=False,
            request_params={},
            deferred_terminal_has_event=False,
        )
        is True
    )
    assert (
        _should_facade_silent_complete_rescue(
            is_team_mode=False,
            suppress_a2ui_stream=False,
            saw_invocation_paused=False,
            has_facade_visible_text=False,
            request_params={},
            deferred_terminal_has_event=False,
            saw_chat_error=True,
        )
        is False
    )


def test_deep_adapter_fallback_follows_runtime_language() -> None:
    adapter = JiuWenSwarmDeepAdapter.__new__(JiuWenSwarmDeepAdapter)
    adapter._resolve_runtime_language = MagicMock(return_value="en")
    assert adapter._reasoning_only_empty_reply_fallback().startswith("No visible reply")
    adapter._resolve_runtime_language.return_value = "zh"
    assert adapter._reasoning_only_empty_reply_fallback().startswith("本轮未生成可见回复")
