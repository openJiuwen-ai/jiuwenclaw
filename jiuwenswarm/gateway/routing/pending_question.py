# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Questions asked through ``ask_user`` and awaiting a plain-text reply.

Deliberately **not** :class:`PendingInteraction`. That record exists for the
digital-avatar follow-up, which resumes by injecting the answer as a new user
message. An ``ask_user`` answer must instead satisfy a blocked tool call via
``chat.send`` with ``source="ask_user_interrupt"`` -- the same resume path Web
and the CLI use. ``chat.user_answer`` only covers evolution approvals on the
deep adapter; using it here leaves the tool hung. Issue #1976 is what happens
when the avatar path and the tool-resume path are confused, so they do not
share a store.

State is in memory, matching Feishu's ``_user_question_card``. A gateway restart
loses pending questions, which is the same exposure the card path already
carries and is preferable to persisting a record whose only consumer is a
single process's in-flight tool call.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

#: A question nobody answers should not shadow ordinary messages forever.
DEFAULT_TTL_SECONDS = 30 * 60

#: Default interrupt source for tool-backed ``ask_user`` questions.
DEFAULT_ASK_USER_SOURCE = "ask_user_interrupt"

_LEADING_INDEX = re.compile(r"^\s*[（(\[]?(\d{1,2})[)\].、:：]?\s*$")

#: The label StructuredAskUserRail always appends to a structured question's
#: options (see interrupt_helpers._build_multi_questions). Selecting it is a
#: request for custom input, not an answer by itself.
_OTHER_LABEL = "other"


@dataclass(frozen=True)
class PendingQuestion:
    """One unanswered ``ask_user`` question on a text-only channel."""

    request_id: str
    channel_id: str
    conversation_key: str
    options: tuple[str, ...]
    source: str = DEFAULT_ASK_USER_SOURCE
    question: str = ""
    created_at: float = field(default_factory=time.time)

    def is_expired(self, ttl: float = DEFAULT_TTL_SECONDS, *, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) - self.created_at > ttl


def match_reply(text: str, options: tuple[str, ...]) -> Optional[str]:
    """Map a reply to the option it selects, or ``None`` if it selects none.

    Three spellings are accepted, in this order: the bare index (``2``, ``2.``,
    ``(2)``), the option label itself, and a case-insensitive label match. A
    reply that matches nothing returns ``None`` so the caller can let it through
    as an ordinary message -- silently swallowing it would be a new way to lose
    a user's words, which is the failure this whole feature is about.

    A question with no options accepts anything non-empty: the reply *is* the
    answer.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None
    if not options:
        return stripped

    index_match = _LEADING_INDEX.match(stripped)
    if index_match:
        index = int(index_match.group(1))
        if 1 <= index <= len(options):
            return options[index - 1]
        # A number outside the range is a miss, not the first option.
        return None

    for label in options:
        if stripped == label:
            return label
    lowered = stripped.lower()
    for label in options:
        if lowered == label.lower():
            return label
    return None


def _is_other_option(label: str) -> bool:
    return label.strip().lower() == _OTHER_LABEL


def resolve_reply(text: str, options: tuple[str, ...]) -> Optional[tuple[str, str]]:
    """Map a reply to ``(answer, custom_input)``, or ``None`` if it answers nothing.

    Structured ``ask_user`` payloads always append an "Other" option
    (StructuredAskUserRail / ``_build_multi_questions``). Three outcomes,
    layered on top of :func:`match_reply`:

    - The reply selects a real (non-"Other") option by index or label: that
      option, with no custom input -- unchanged from before Other existed.
    - The reply selects "Other" itself, bare (its index or its label, with
      nothing else): not a complete answer. "Other" exists to carry custom
      text, and StructuredAskUserRail rejects a bare "Other" as an empty
      answer (#2330); returning ``None`` here leaves the question pending
      instead of resuming the tool call with nothing useful.
    - Anything else, when "Other" is offered: the whole reply is custom text
      for "Other", matching the shape Web/CLI send
      (``selected_options=["Other"]`` + ``custom_input``).

    A question with no options keeps taking the whole reply verbatim -- there
    is no "Other" concept without options.
    """
    stripped = (text or "").strip()
    if not stripped:
        return None

    matched = match_reply(stripped, options)
    if matched is not None:
        if options and _is_other_option(matched):
            return None
        return (matched, "")

    if any(_is_other_option(label) for label in options):
        return ("Other", stripped)
    return None


class PendingQuestionRegistry:
    """Per-conversation store of unanswered questions.

    One question per conversation at a time: a second question replaces the
    first, because the agent can only be blocked on one ``ask_user`` call in a
    conversation and a stale entry would steal the reply meant for the new one.
    """

    def __init__(self, ttl: float = DEFAULT_TTL_SECONDS) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._pending: dict[tuple[str, str], PendingQuestion] = {}

    @staticmethod
    def _key(channel_id: str, conversation_key: str) -> tuple[str, str]:
        return (str(channel_id or "").strip().lower(), str(conversation_key or "").strip())

    def register(
        self,
        *,
        request_id: str,
        channel_id: str,
        conversation_key: str,
        options: tuple[str, ...],
        source: str = DEFAULT_ASK_USER_SOURCE,
        question: str = "",
    ) -> PendingQuestion:
        entry = PendingQuestion(
            request_id=request_id,
            channel_id=str(channel_id or ""),
            conversation_key=str(conversation_key or ""),
            options=tuple(options),
            source=str(source or DEFAULT_ASK_USER_SOURCE).strip() or DEFAULT_ASK_USER_SOURCE,
            question=str(question or "").strip(),
        )
        with self._lock:
            self._pending[self._key(channel_id, conversation_key)] = entry
        return entry

    def peek(self, channel_id: str, conversation_key: str) -> Optional[PendingQuestion]:
        """The live question for this conversation, dropping it if expired."""
        key = self._key(channel_id, conversation_key)
        with self._lock:
            entry = self._pending.get(key)
            if entry is None:
                return None
            if entry.is_expired(self._ttl):
                self._pending.pop(key, None)
                return None
            return entry

    def resolve(self, channel_id: str, conversation_key: str, text: str):
        """Match a reply against the pending question.

        Returns ``(question, answer_label, custom_input)`` when the reply
        answers it, and ``None`` when there is nothing pending or the reply is
        not an answer (including a bare "Other" selection, which is not a
        complete answer). The entry is only removed on a match -- a stray
        message must not consume the question the user has yet to answer.
        """
        entry = self.peek(channel_id, conversation_key)
        if entry is None:
            return None
        resolved = resolve_reply(text, entry.options)
        if resolved is None:
            return None
        answer, custom_input = resolved
        with self._lock:
            self._pending.pop(self._key(channel_id, conversation_key), None)
        return entry, answer, custom_input

    def discard(self, channel_id: str, conversation_key: str) -> None:
        with self._lock:
            self._pending.pop(self._key(channel_id, conversation_key), None)

    def cleanup_expired(self) -> int:
        now = time.time()
        with self._lock:
            stale = [k for k, v in self._pending.items() if v.is_expired(self._ttl, now=now)]
            for key in stale:
                self._pending.pop(key, None)
        return len(stale)


#: Process-wide registry. The gateway is a single process; Feishu's card cache
#: is scoped the same way.
PENDING_QUESTIONS = PendingQuestionRegistry()


def build_interrupt_resume_params(
    request_id: str,
    answer: str,
    *,
    source: str = DEFAULT_ASK_USER_SOURCE,
    question: str = "",
    custom_input: str = "",
) -> dict:
    """The ``chat.send`` params that resume a blocked ``ask_user`` tool call.

    Matches what the CLI and Web send (``source="ask_user_interrupt"`` over
    streaming ``chat.send``). ``chat.user_answer`` is the wrong path: the deep
    adapter's ``handle_user_answer`` only resolves evolution approvals.

    ``custom_input`` carries free text typed for the "Other" option --
    ``interface.py``'s ``_build_interactive_input_from_answers`` reads it
    alongside ``selected_options`` to recover the user's actual words instead
    of the literal placeholder "Other".
    """
    answer_entry: dict = {"selected_options": [answer]}
    question_text = str(question or "").strip()
    if question_text:
        answer_entry["question"] = question_text
    custom_input_text = str(custom_input or "").strip()
    if custom_input_text:
        answer_entry["custom_input"] = custom_input_text
    return {
        "query": "",
        "request_id": request_id,
        "answers": [answer_entry],
        "source": str(source or DEFAULT_ASK_USER_SOURCE).strip() or DEFAULT_ASK_USER_SOURCE,
    }


__all__ = [
    "DEFAULT_ASK_USER_SOURCE",
    "DEFAULT_TTL_SECONDS",
    "PENDING_QUESTIONS",
    "PendingQuestion",
    "PendingQuestionRegistry",
    "build_interrupt_resume_params",
    "match_reply",
    "resolve_reply",
]
