# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plain-text notice for ``context.compression_state``, shared by every IM channel.

Web and the TUI render compression inline with a rich, multi-part line. The nine
IM platforms would otherwise render nothing: the agent drops history out from
under the conversation and the human has no way to know. This module turns the
event into a sentence a chat client can send.

**What IM announces**

- ``started`` once occupancy reaches the processor's own
  ``trigger_context_ratio``: a short "Context N% — compacting…" line so the user
  sees occupancy before history rewrites.
- ``completed`` / ``compressed`` / ``failed``: outcome lines (tokens freed, or
  failure warning).

**What stays silent**

- ``noop`` always.
- ``started`` without a usable percent, or below the configured ratio — avoids
  unquantified "compacting…" noise and most started→noop orphan lines.

The threshold is read from the same config section the compressor reads, per
processor, rather than being a constant. It used to be a hard-coded 80, which
matched only the default ratio: an operator who lowered ``trigger_context_ratio``
kept the compaction and the outcome line and silently lost the warning.
"""
from __future__ import annotations

import time
from typing import Any, Mapping, Optional

from jiuwenswarm.common.utils import logger

# Channels that render ``context.compression_state`` themselves, richly and
# inline. They must keep receiving the raw event; only the channels that would
# otherwise drop it on the floor get the plain-text substitute.
_RICH_RENDERERS = frozenset({"web", "tui", "acp", "ssh"})

# Statuses that mean the conversation history actually changed.
_DONE = {"completed", "compressed"}
# Statuses that mean it tried and did not finish; the user should know, because
# the next turn may hit a context limit that compaction was meant to avoid.
_FAILED = {"failed", "error"}

# The occupancy at which a compressor fires, per processor, read from the same
# config the compressor reads. A hard-coded 80 here would silently stop
# announcing the moment an operator lowered ``trigger_context_ratio``: the
# compaction would still happen, the outcome line would still arrive, and only
# the warning the user actually needs would go missing.
_PROCESSOR_CONFIG_SECTIONS = {
    "DialogueCompressor": "dialogue_compressor_config",
    "CurrentRoundCompressor": "current_round_compressor_config",
    "RoundLevelCompressor": "round_level_compressor_config",
    "SessionMemoryCompressor": "session_memory_config",
}

# Class default in the compressor tree; used when a section omits the key.
_DEFAULT_TRIGGER_CONTEXT_RATIO = 0.8

# The config is a YAML read behind a lock and this runs per event, so the parsed
# ratios are cached. The TTL is short because ``enable_reload`` lets an operator
# change the ratio without a restart -- a stale threshold for one minute is a
# fair trade for not re-reading a file on every compression event.
_RATIO_CACHE_TTL_SECONDS = 60.0
_ratio_cache: dict[str, float] | None = None
_ratio_cache_at = 0.0


def reset_trigger_ratio_cache() -> None:
    """Drop the cached ratios. For tests and for an explicit config reload."""
    global _ratio_cache, _ratio_cache_at
    _ratio_cache = None
    _ratio_cache_at = 0.0


def _load_trigger_ratios() -> dict[str, float]:
    """Read ``trigger_context_ratio`` per processor from the effective config.

    The section lives under ``react``, not at the top level: the deep adapter
    builds its context rail from ``config_base["react"]``, so that is the only
    place a value here can agree with the compressor that fires. Reading the top
    level instead returns nothing and falls back to the default for every
    processor -- which looks like it works and silently restores the constant
    this function exists to remove.
    """
    ratios: dict[str, float] = {}
    try:
        from jiuwenswarm.common.config import get_config

        react_cfg = (get_config() or {}).get("react") or {}
        if not isinstance(react_cfg, Mapping):
            return ratios
        engine_cfg = react_cfg.get("context_engine_config") or {}
        if not isinstance(engine_cfg, Mapping):
            return ratios
        for processor, section in _PROCESSOR_CONFIG_SECTIONS.items():
            cfg = engine_cfg.get(section)
            if not isinstance(cfg, Mapping):
                continue
            value = _as_number(cfg.get("trigger_context_ratio"))
            # A ratio outside (0, 1] is not a ratio; ignore it rather than
            # computing a threshold no percentage can ever cross.
            if value is not None and 0 < value <= 1:
                ratios[processor] = value
    except Exception:  # noqa: BLE001 - a notice must never break delivery
        logger.debug("[compression] could not read trigger ratios", exc_info=True)
    return ratios


def started_notice_min_percent(processor: str) -> float:
    """Occupancy, in percent, at or above which ``started`` is worth announcing.

    Equals the processor's own trigger ratio, so the notice fires exactly when
    the compaction does instead of on a constant that only matched the default.
    """
    global _ratio_cache, _ratio_cache_at

    now = time.monotonic()
    if _ratio_cache is None or now - _ratio_cache_at >= _RATIO_CACHE_TTL_SECONDS:
        _ratio_cache = _load_trigger_ratios()
        _ratio_cache_at = now

    ratio = _ratio_cache.get(processor, _DEFAULT_TRIGGER_CONTEXT_RATIO)
    return ratio * 100

_PROCESSOR_LABELS = {
    "DialogueCompressor": "earlier messages",
    "CurrentRoundCompressor": "the current round",
    "RoundLevelCompressor": "the whole conversation",
    "SessionMemoryCompressor": "session memory",
}


def _as_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_number(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def format_compression_notice(payload: Mapping[str, Any]) -> Optional[str]:
    """One line for a chat channel, or None when nothing should be posted.

    Announces high-occupancy ``started`` and compaction outcomes; returns None
    for ``noop`` and low/unknown-occupancy ``started``. Never raises: a
    malformed payload yields None rather than breaking delivery of the turn.
    """
    if not isinstance(payload, Mapping):
        return None

    status = str(payload.get("status") or "").strip().lower()
    processor = str(payload.get("processor") or "").strip()
    what = _PROCESSOR_LABELS.get(processor, "conversation history")

    if status in _FAILED:
        return f"⚠️ Could not compact {what} — this turn may run short of context."

    before = payload.get("before") if isinstance(payload.get("before"), Mapping) else {}

    if status == "started":
        pct = _as_number(before.get("context_percent"))
        if pct is None or pct < started_notice_min_percent(processor):
            return None
        return f"🗜️ Context {round(pct)}% — compacting…"

    if status not in _DONE:
        return None

    after = payload.get("after") if isinstance(payload.get("after"), Mapping) else {}
    before_tokens = _as_int(before.get("tokens"))
    after_tokens = _as_int(after.get("tokens"))

    # Without both sides there is no saving to quote, so say the plain fact.
    if before_tokens is None or after_tokens is None or before_tokens <= 0:
        return f"🗜️ Compacted {what} to free up context."

    saved = before_tokens - after_tokens
    if saved <= 0:
        # Reported done but nothing shrank. Do not claim a saving that did not
        # happen; the honest line is that history changed shape.
        return f"🗜️ Compacted {what}."

    pct = round(saved * 100 / before_tokens)
    return (
        f"🗜️ Compacted {what}: {_format_tokens(before_tokens)} → "
        f"{_format_tokens(after_tokens)} tokens ({pct}% freed)."
    )


def channel_renders_compression(channel_id: Any) -> bool:
    """Whether this channel already displays compression events on its own."""
    return str(channel_id or "").strip().lower() in _RICH_RENDERERS


# Metadata keys that bind a message into an in-flight stream reply. Keeping them
# on the notice would make WeCom mash the line into reply_stream, or make Feishu
# treat it as the chat.final that closes the card buffer. Xiaoyi uses
# xiaoyi_task_id as the A2A artifact stream key — a CHAT_FINAL with that id
# flushes the in-flight answer buffer into the notice.
_STREAM_BINDING_META_KEYS = frozenset({
    "wecom_req_id",
    "xiaoyi_task_id",
})


def as_text_message(msg: Any, *, delivery_channel_id: str | None = None) -> Any | None:
    """Rewrite a compression event into a plain-text message, or drop it.

    Returns the original message when it is not a compression event, a rewritten
    copy shaped like an ordinary ``chat.final`` text reply, or ``None`` when
    this event should not be delivered to a text-only channel at all.

    Content alone is not enough: WeCom and WeChat only deliver ``CHAT_FINAL`` /
    plain ``res`` messages, and stream-bound ids/metadata would fold the notice
    into the in-flight answer. The rewrite therefore also sets
    ``event_type=CHAT_FINAL``, gives the notice its own id, and strips stream
    binding keys so each channel's existing text path posts a separate line.
    """
    payload = getattr(msg, "payload", None)
    if not isinstance(payload, Mapping):
        return msg
    event_type = str(payload.get("event_type") or getattr(msg, "event_type", "") or "")
    if not event_type.endswith("context.compression_state"):
        return msg

    if channel_renders_compression(
        delivery_channel_id if delivery_channel_id is not None else getattr(msg, "channel_id", None)
    ):
        return msg

    notice = format_compression_notice(payload)
    if not notice:
        # noop / low-occupancy started: nothing worth interrupting the thread.
        return None

    status = str(payload.get("status") or "").strip().lower()
    id_suffix = "compaction-started" if status == "started" else "compaction"

    try:
        from dataclasses import replace

        from jiuwenswarm.common.schema.message import EventType

        orig_id = str(getattr(msg, "id", "") or "msg")
        meta = getattr(msg, "metadata", None)
        cleaned_meta: dict[str, Any] | None = None
        if isinstance(meta, Mapping):
            cleaned_meta = {
                k: v for k, v in meta.items() if k not in _STREAM_BINDING_META_KEYS
            }
            # WeChat clears the delta accumulator on every CHAT_FINAL; mark this
            # as a standalone line so that path keeps the in-flight answer.
            cleaned_meta["standalone_notice"] = True
        else:
            cleaned_meta = {"standalone_notice": True}

        return replace(
            msg,
            id=f"{orig_id}-{id_suffix}",
            type="res",
            event_type=EventType.CHAT_FINAL,
            params={"content": notice},
            payload={
                "event_type": EventType.CHAT_FINAL.value,
                "content": notice,
            },
            metadata=cleaned_meta,
        )
    except Exception:  # noqa: BLE001 - never break delivery over a notice
        logger.warning(
            "[compression_notice] failed to rewrite event id=%s; using minimal fallback",
            getattr(msg, "id", ""),
            exc_info=True,
        )
        # Last resort: deliver the notice text without metadata surgery.
        try:
            from dataclasses import replace

            from jiuwenswarm.common.schema.message import EventType

            return replace(
                msg,
                id=f"{getattr(msg, 'id', 'msg')}-{id_suffix}",
                type="res",
                event_type=EventType.CHAT_FINAL,
                params={"content": notice},
                payload={
                    "event_type": EventType.CHAT_FINAL.value,
                    "content": notice,
                },
                metadata={"standalone_notice": True},
            )
        except Exception:  # noqa: BLE001
            return None


def prepare_outbound_message(msg: Any, *, delivery_channel_id: str | None = None) -> Any | None:
    """Return message to send, or None to drop."""
    return as_text_message(msg, delivery_channel_id=delivery_channel_id)
