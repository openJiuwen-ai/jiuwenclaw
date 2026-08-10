# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""IM-channel notice for context compaction."""

from __future__ import annotations

import pytest

from jiuwenswarm.common.schema import Message
from jiuwenswarm.common.schema.message import EventType
from jiuwenswarm.gateway.channel_manager.im_platforms.platform_adapter.compression_notice import (
    as_text_message,
    channel_renders_compression,
    format_compression_notice,
    reset_trigger_ratio_cache,
    started_notice_min_percent,
)


@pytest.fixture(autouse=True)
def _clear_ratio_cache():
    """The threshold is cached, so one test must not decide another's answer."""
    reset_trigger_ratio_cache()
    yield
    reset_trigger_ratio_cache()


def _config_with(section: str, ratio) -> dict:
    return {"react": {"context_engine_config": {section: {"trigger_context_ratio": ratio}}}}


def _event(status: str, *, before: int | None = None, after: int | None = None,
           processor: str = "DialogueCompressor") -> dict:
    payload: dict = {
        "event_type": "context.compression_state",
        "status": status,
        "processor": processor,
    }
    if before is not None:
        payload["before"] = {"tokens": before, "messages": 12}
    if after is not None:
        payload["after"] = {"tokens": after, "messages": 4}
    return payload


def test_started_without_high_occupancy_stays_silent() -> None:
    """started with only tokens (or low %) must not post unquantified compacting."""
    assert format_compression_notice(_event("started", before=3146)) is None
    assert format_compression_notice({
        "event_type": "context.compression_state",
        "status": "started",
        "processor": "DialogueCompressor",
        "before": {"tokens": 10_000, "context_percent": 79},
    }) is None
    assert format_compression_notice(_event("noop", before=3146, after=3146)) is None


def test_started_with_high_occupancy_announces_compacting() -> None:
    notice = format_compression_notice({
        "event_type": "context.compression_state",
        "status": "started",
        "processor": "DialogueCompressor",
        "before": {"tokens": 100_000, "context_percent": 85},
    })
    assert notice is not None
    assert "85%" in notice
    assert "compacting" in notice.lower()


# ------------------------------------------------- threshold follows config


def test_a_lowered_trigger_ratio_still_announces(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bug this replaces: compaction fired and the warning went missing.

    With the threshold hard-coded at 80, an operator who compacts at 50% kept
    the outcome line and silently lost the heads-up.
    """
    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: _config_with("dialogue_compressor_config", 0.5),
    )
    notice = format_compression_notice({
        "event_type": "context.compression_state",
        "status": "started",
        "processor": "DialogueCompressor",
        "before": {"tokens": 50_000, "context_percent": 52},
    })
    assert notice is not None
    assert "52%" in notice


def test_a_raised_trigger_ratio_stays_silent_below_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: _config_with("dialogue_compressor_config", 0.95),
    )
    assert format_compression_notice({
        "event_type": "context.compression_state",
        "status": "started",
        "processor": "DialogueCompressor",
        "before": {"tokens": 90_000, "context_percent": 90},
    }) is None


def test_each_processor_reads_its_own_section(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three compressors, three ratios; the notice must not use one for all."""
    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: {
            "react": {
                "context_engine_config": {
                    "dialogue_compressor_config": {"trigger_context_ratio": 0.5},
                    "round_level_compressor_config": {"trigger_context_ratio": 0.9},
                }
            }
        },
    )
    assert started_notice_min_percent("DialogueCompressor") == 50
    assert started_notice_min_percent("RoundLevelCompressor") == 90
    # No section of its own -> the compressor-tree default.
    assert started_notice_min_percent("CurrentRoundCompressor") == 80


@pytest.mark.parametrize("ratio", [0, -0.5, 1.5, "high", None])
def test_a_ratio_that_is_not_a_ratio_falls_back(
    monkeypatch: pytest.MonkeyPatch, ratio,
) -> None:
    """A threshold no percentage can cross would silence the notice forever."""
    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: _config_with("dialogue_compressor_config", ratio),
    )
    assert started_notice_min_percent("DialogueCompressor") == 80


def test_an_unreadable_config_never_breaks_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom():
        raise OSError("config gone")

    monkeypatch.setattr("jiuwenswarm.common.config.get_config", _boom)
    assert started_notice_min_percent("DialogueCompressor") == 80


def test_the_threshold_is_cached_between_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This runs per compression event; it must not re-read YAML each time."""
    calls = {"n": 0}

    def _counted():
        calls["n"] += 1
        return _config_with("dialogue_compressor_config", 0.6)

    monkeypatch.setattr("jiuwenswarm.common.config.get_config", _counted)
    for _ in range(5):
        assert started_notice_min_percent("DialogueCompressor") == 60
    assert calls["n"] == 1

    reset_trigger_ratio_cache()
    assert started_notice_min_percent("DialogueCompressor") == 60
    assert calls["n"] == 2


def test_a_real_compaction_quotes_what_it_freed() -> None:
    notice = format_compression_notice(_event("completed", before=100_000, after=25_000))
    assert notice is not None
    assert "100.0k" in notice
    assert "25.0k" in notice
    assert "75% freed" in notice
    assert "earlier messages" in notice


def test_done_without_numbers_still_tells_the_user() -> None:
    """Missing before/after must not silence the notice — the history still changed."""
    notice = format_compression_notice(_event("completed"))
    assert notice is not None
    assert "Compacted" in notice


def test_done_that_shrank_nothing_claims_no_saving() -> None:
    notice = format_compression_notice(_event("completed", before=5_000, after=5_000))
    assert notice is not None
    assert "freed" not in notice


def test_failure_is_announced_because_the_next_turn_may_suffer() -> None:
    notice = format_compression_notice(_event("failed", processor="RoundLevelCompressor"))
    assert notice is not None
    assert "Could not compact" in notice
    assert "the whole conversation" in notice


def test_unknown_processor_falls_back_to_generic_wording() -> None:
    notice = format_compression_notice(_event("completed", processor="SomeNewCompressor"))
    assert notice is not None
    assert "conversation history" in notice


def test_malformed_payloads_never_raise() -> None:
    """A bad payload must not break delivery of the turn it rode in on."""
    assert format_compression_notice({}) is None
    assert format_compression_notice({"status": None}) is None
    assert format_compression_notice({"status": "completed", "before": "not-a-dict"}) is not None
    assert format_compression_notice({"status": "completed", "before": {"tokens": "x"}}) is not None


def test_token_scaling_is_readable() -> None:
    notice = format_compression_notice(_event("completed", before=2_000_000, after=500_000))
    assert notice is not None
    assert "2.0M" in notice
    assert "500.0k" in notice


# ------------------------------------------------- dispatch-point substitution


def _msg(channel_id: str, payload: dict) -> Message:
    return Message(
        id="evt-1",
        type="event",
        channel_id=channel_id,
        session_id="s1",
        params={},
        timestamp=0.0,
        ok=True,
        payload=payload,
    )


def test_rich_renderers_keep_the_raw_event() -> None:
    """Web and the TUI already render compression inline and must not be downgraded."""
    for channel in ("web", "tui", "acp", "ssh"):
        assert channel_renders_compression(channel)
        original = _msg(channel, _event("completed", before=100_000, after=25_000))
        assert as_text_message(original) is original


def test_acp_raw_event_keeps_context_compression_state_event_type() -> None:
    """ACP is a rich renderer: the raw event must reach it untouched so the
    downstream ACP session/update builder (build_acp_session_update) can
    still recognize ``context.compression_state`` by its real event_type
    instead of receiving a CHAT_FINAL rewrite that would end the turn.
    """
    original = Message(
        id="evt-acp-1",
        type="event",
        channel_id="acp",
        session_id="s1",
        params={},
        timestamp=0.0,
        ok=True,
        payload=_event("completed", before=100_000, after=25_000),
        event_type=EventType.CONTEXT_COMPRESSION_STATE,
    )
    out = as_text_message(original)
    assert out is original
    assert out.event_type == EventType.CONTEXT_COMPRESSION_STATE
    assert out.payload["event_type"] == "context.compression_state"


def test_im_channels_receive_the_notice_as_payload_content() -> None:
    """payload["content"] is the field every IM channel reads to extract text."""
    for channel in ("feishu", "slack", "telegram", "discord", "whatsapp",
                    "wecom", "wechat", "dingtalk", "xiaoyi"):
        assert not channel_renders_compression(channel)
        out = as_text_message(_msg(channel, _event("completed", before=100_000, after=25_000)))
        assert out is not None, channel
        assert "75% freed" in out.payload["content"], channel
        assert out.params["content"] == out.payload["content"], channel


def test_im_notice_is_shaped_like_ordinary_chat_final() -> None:
    """WeCom/WeChat only deliver CHAT_FINAL / plain res; content alone is dropped."""
    original = Message(
        id="req-42",
        type="event",
        channel_id="wecom",
        session_id="s1",
        params={},
        timestamp=0.0,
        ok=True,
        payload=_event("completed", before=100_000, after=25_000),
        metadata={"wecom_req_id": "stream-1", "chat_type": "dm"},
    )
    out = as_text_message(original)
    assert out is not None
    assert out is not original
    assert out.type == "res"
    assert out.event_type == EventType.CHAT_FINAL
    assert out.id == "req-42-compaction"
    assert out.payload["event_type"] == "chat.final"
    assert "wecom_req_id" not in (out.metadata or {})
    assert out.metadata.get("standalone_notice") is True
    assert out.metadata.get("chat_type") == "dm"


def test_events_worth_no_notice_are_not_delivered_to_im_channels() -> None:
    """Low-occupancy started and noop stay dropped on IM."""
    assert as_text_message(_msg("slack", _event("started", before=3146))) is None
    assert as_text_message(_msg("slack", _event("noop", before=3146, after=3146))) is None


def test_high_occupancy_started_is_rewritten_for_im() -> None:
    """Surface context N% — compacting as a standalone IM line."""
    original = Message(
        id="req-1",
        type="event",
        channel_id="xiaoyi",
        session_id="s1",
        params={},
        timestamp=0.0,
        ok=True,
        payload={
            "event_type": "context.compression_state",
            "status": "started",
            "processor": "DialogueCompressor",
            "before": {"tokens": 100_000, "context_percent": 90.4},
        },
        metadata={
            "xiaoyi_task_id": "task-ABC",
            "xiaoyi_session_id": "sid-1",
        },
    )
    out = as_text_message(original, delivery_channel_id="xiaoyi")
    assert out is not None
    assert out.id == "req-1-compaction-started"
    assert out.type == "res"
    assert out.event_type == EventType.CHAT_FINAL
    assert "90%" in out.payload["content"]
    assert "compacting" in out.payload["content"].lower()
    assert out.metadata.get("standalone_notice") is True
    assert "xiaoyi_task_id" not in (out.metadata or {})
    assert out.metadata.get("xiaoyi_session_id") == "sid-1"


def test_delivery_channel_id_overrides_msg_channel_for_rich_renderer_check() -> None:
    """Compression from a web session must still become plain text on Slack."""
    msg = Message(
        id="evt-1",
        type="event",
        channel_id="web",  # origin channel
        session_id="s1",
        params={},
        timestamp=0.0,
        ok=True,
        payload={
            "event_type": "context.compression_state",
            "status": "completed",
            "processor": "DialogueCompressor",
            "before": {"tokens": 100_000},
            "after": {"tokens": 25_000},
        },
    )
    out = as_text_message(msg, delivery_channel_id="slack")
    assert out is not None
    assert out.event_type == EventType.CHAT_FINAL
    assert "75% freed" in out.payload["content"]


def test_rewrite_strips_xiaoyi_stream_binding_keys() -> None:
    """Notices must not share the in-flight A2A task stream key."""
    original = Message(
        id="req-1",
        type="event",
        channel_id="xiaoyi",
        session_id="s1",
        params={},
        timestamp=0.0,
        ok=True,
        payload=_event("completed", before=10_000, after=2_000),
        metadata={
            "xiaoyi_task_id": "task-ABC",
            "xiaoyi_session_id": "sid-1",
            "wecom_req_id": "stream-1",
            "chat_type": "dm",
        },
    )
    out = as_text_message(original, delivery_channel_id="xiaoyi")
    assert out is not None
    assert out.id == "req-1-compaction"
    assert "xiaoyi_task_id" not in (out.metadata or {})
    assert "wecom_req_id" not in (out.metadata or {})
    assert out.metadata.get("xiaoyi_session_id") == "sid-1"
    assert out.metadata.get("standalone_notice") is True
    assert out.metadata.get("chat_type") == "dm"


def test_unrelated_messages_pass_through_untouched() -> None:
    """The dispatch point sees every outbound message; only this event may change."""
    other = _msg("slack", {"event_type": "chat.final", "content": "hello"})
    assert as_text_message(other) is other

    no_payload = Message(
        id="x", type="event", channel_id="slack", session_id="s",
        params={}, timestamp=0.0, ok=True,
    )
    assert as_text_message(no_payload) is no_payload


def test_rewrite_falls_back_when_primary_replace_fails(monkeypatch) -> None:
    """If metadata surgery fails, still deliver a minimal notice with the text."""
    import dataclasses

    calls = {"n": 0}
    real_replace = dataclasses.replace

    def flaky_replace(obj, /, **changes):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated replace failure")
        return real_replace(obj, **changes)

    monkeypatch.setattr(dataclasses, "replace", flaky_replace)

    out = as_text_message(
        _msg("slack", _event("completed", before=100_000, after=25_000))
    )
    assert out is not None
    assert out.event_type == EventType.CHAT_FINAL
    assert "75% freed" in out.payload["content"]
    assert out.metadata.get("standalone_notice") is True
