# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Render an ``ask_user`` question as plain text for channels with no card.

The ``ask_user`` tool emits ``chat.ask_user_question``. Only Feishu renders it, as
an interactive card; the other eight IM platforms have no handler, so the event
reaches ``send()`` and produces nothing. The agent is meanwhile blocked on the
tool result -- which makes this a silent hang, not a missing notification.

This module turns the question into a numbered message that any text channel
already knows how to deliver. It is deliberately the *outbound* half only: the
reply comes back as an ordinary message and is correlated separately, and the
resume itself uses streaming ``chat.send`` with ``source="ask_user_interrupt"``
-- the same path Web and the CLI use. ``chat.user_answer`` does not resume a
blocked ask_user tool call on the deep adapter.

Do not confuse this with the digital-avatar follow-up in ``im_pipeline``. That
mechanism is triggered by a regex on the model's own text, and resumes by
injecting the answer as a *new user message*. An ``ask_user`` answer must
instead satisfy a tool call keyed by ``tool_call_id``; routing one through the
other is the failure recorded at ``interrupt_helpers.py:304-312``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Channels that render a question natively and must not receive the text form.
# Feishu draws a card; the rest are rich clients with their own question UI.
_RICH_RENDERERS = frozenset({"feishu", "web", "tui", "acp", "ssh"})

# Metadata keys that bind a message into an in-flight stream reply. Carried over
# from the compaction notice: leaving them on a standalone message makes WeCom
# fold it into the streaming answer and makes Feishu treat it as the chat.final
# that closes the card buffer.
_STREAM_BINDING_META_KEYS = frozenset({
    "wecom_req_id",
})

_EVENT_SUFFIX = "chat.ask_user_question"

# A question with more options than this is not usable as a numbered list in a
# chat window; the text still lists them, but it is worth a log line.
_MANY_OPTIONS = 12

_DEFAULT_SOURCE = "ask_user_interrupt"


@dataclass(frozen=True)
class ParsedQuestion:
    """The parts of an ``ask_user`` payload this module needs."""

    request_id: str
    question: str
    #: Option labels, in the order presented. Empty means free-text.
    options: tuple[str, ...]
    header: str = ""
    source: str = _DEFAULT_SOURCE


def channel_renders_questions(channel_id: Any) -> bool:
    """Whether this channel draws its own question UI."""
    return str(channel_id or "").strip().lower() in _RICH_RENDERERS


def parse_question(payload: Any) -> Optional[ParsedQuestion]:
    """Pull the first question out of an ``ask_user`` payload.

    Only the first is used, matching Feishu's card: the tool asks one thing at a
    time in practice, and a numbered list of two interleaved option sets could
    not be answered unambiguously by ``2``.

    A missing ``request_id`` is rejected: without it the resume cannot satisfy
    the blocked tool call.
    """
    if not isinstance(payload, Mapping):
        return None
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id:
        return None
    questions = payload.get("questions")
    if not isinstance(questions, (list, tuple)) or not questions:
        return None
    first = questions[0]
    if not isinstance(first, Mapping):
        return None

    text = str(first.get("question") or "").strip()
    labels: list[str] = []
    raw_options = first.get("options")
    if isinstance(raw_options, (list, tuple)):
        for option in raw_options:
            if isinstance(option, Mapping):
                label = str(option.get("label") or "").strip()
            else:
                label = str(option or "").strip()
            if label:
                labels.append(label)

    if not text and not labels:
        return None
    if len(labels) > _MANY_OPTIONS:
        logger.info(
            "[AskUserNotice] question has %d options; a numbered reply will be awkward",
            len(labels),
        )
    source = str(payload.get("source") or _DEFAULT_SOURCE).strip() or _DEFAULT_SOURCE
    return ParsedQuestion(
        request_id=request_id,
        question=text,
        options=tuple(labels),
        header=str(first.get("header") or "").strip(),
        source=source,
    )


def format_question_notice(parsed: ParsedQuestion) -> str:
    """Render the question as a numbered prompt.

    A free-text question says what is expected instead of printing an empty
    list -- an unanswered question is the bug this feature exists to fix, and a
    prompt with nothing to reply to reproduces it in a new way.
    """
    lines: list[str] = []
    if parsed.header:
        lines.append(f"【{parsed.header}】")
    if parsed.question:
        lines.append(parsed.question)

    if parsed.options:
        lines.append("")
        for index, label in enumerate(parsed.options, start=1):
            lines.append(f"{index}. {label}")
        lines.append("")
        lines.append("Reply with the number of your choice.")
    else:
        lines.append("")
        lines.append("Reply to this message with your answer.")
    return "\n".join(lines).strip()


def as_text_message(msg: Any, *, channel_id: Any = None) -> Any:
    """Rewrite an ``ask_user`` event into a plain-text message, or drop it.

    Returns the original message when it is not a question event or when the
    channel renders one natively, a rewritten copy shaped like an ordinary
    ``chat.final`` text reply, or ``None`` when the payload carries no question
    worth sending.

    ``channel_id`` overrides ``msg.channel_id`` for the rich-renderer check so
    team fan-out can rewrite for the physical delivery channel even when the
    originator channel draws its own card.

    Content alone is not enough. WeCom and WeChat only deliver ``CHAT_FINAL`` /
    plain ``res`` messages, and stream-bound ids would fold the question into the
    in-flight answer, so the rewrite sets the event type, gives the message its
    own id, and strips the binding keys.
    """
    payload = getattr(msg, "payload", None)
    if not isinstance(payload, Mapping):
        return msg
    event_type = str(payload.get("event_type") or getattr(msg, "event_type", "") or "")
    if not event_type.endswith(_EVENT_SUFFIX):
        return msg

    target_channel = channel_id if channel_id is not None else getattr(msg, "channel_id", None)
    if channel_renders_questions(target_channel):
        return msg

    parsed = parse_question(payload)
    if parsed is None:
        return None

    notice = format_question_notice(parsed)
    if not notice:
        return None

    try:
        from dataclasses import replace

        from jiuwenswarm.common.schema.message import EventType

        orig_id = str(getattr(msg, "id", "") or "msg")
        meta = getattr(msg, "metadata", None)
        if isinstance(meta, Mapping):
            cleaned_meta = {
                k: v for k, v in meta.items() if k not in _STREAM_BINDING_META_KEYS
            }
        else:
            cleaned_meta = {}
        # WeChat clears its delta accumulator on every CHAT_FINAL; mark this as a
        # standalone line so the in-flight answer survives the question.
        cleaned_meta["standalone_notice"] = True
        # Carried so the reply can be correlated back to this exact question.
        cleaned_meta["ask_user_request_id"] = parsed.request_id

        return replace(
            msg,
            id=f"{orig_id}-question",
            type="res",
            event_type=EventType.CHAT_FINAL,
            params={"content": notice},
            payload={
                "event_type": EventType.CHAT_FINAL.value,
                "content": notice,
            },
            metadata=cleaned_meta,
        )
    except Exception:  # noqa: BLE001 - never break delivery over a fallback
        logger.exception("[AskUserNotice] failed to rewrite question for %s", target_channel)
        return None


__all__ = [
    "ParsedQuestion",
    "as_text_message",
    "channel_renders_questions",
    "format_question_notice",
    "parse_question",
]
