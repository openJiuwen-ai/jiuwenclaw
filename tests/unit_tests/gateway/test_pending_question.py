# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Correlating a plain-text reply back to a blocked ``ask_user`` call.

Two rules carry most of the risk. A reply that is not an answer must fall
through as an ordinary message rather than be swallowed, and the answer must be
shaped so it satisfies the tool call -- not so it resumes the conversation with
text, which is what the digital-avatar path does and what issue #1976 was.
"""

from __future__ import annotations

import pytest

from jiuwenswarm.gateway.routing.pending_question import (
    PendingQuestionRegistry,
    build_interrupt_resume_params,
    match_reply,
    resolve_reply,
)

OPTIONS = ("Alpha", "Beta", "Gamma")
OPTIONS_WITH_OTHER = ("Yes", "No", "Other")


# ------------------------------------------------------------ reply matching


@pytest.mark.parametrize("text", ["2", " 2 ", "2.", "(2)", "[2]", "2、", "2:"])
def test_an_index_selects_the_option(text: str) -> None:
    assert match_reply(text, OPTIONS) == "Beta"


def test_a_label_selects_itself() -> None:
    assert match_reply("Gamma", OPTIONS) == "Gamma"


def test_a_label_matches_case_insensitively() -> None:
    assert match_reply("gamma", OPTIONS) == "Gamma"


@pytest.mark.parametrize("text", ["0", "4", "99"])
def test_an_index_outside_the_range_is_not_an_answer(text: str) -> None:
    """Falling back to the first option would answer for the user."""
    assert match_reply(text, OPTIONS) is None


def test_an_unrelated_message_is_not_an_answer() -> None:
    """It must reach the agent as an ordinary message, not vanish."""
    assert match_reply("actually, wait", OPTIONS) is None


def test_an_empty_reply_is_not_an_answer() -> None:
    assert match_reply("   ", OPTIONS) is None
    assert match_reply("", ()) is None


def test_a_free_text_question_takes_the_whole_reply() -> None:
    assert match_reply("  use the second one  ", ()) == "use the second one"


# ------------------------------------------------------- Other / custom_input
#
# Structured ask_user payloads always append an "Other" option (#2330 /
# StructuredAskUserRail). The plain-text fallback must not treat a bare
# "Other" selection as a complete answer, and any free text that is not a
# listed option must resume as custom_input for "Other" -- not
# selected_options=["Other"] alone, which StructuredAskUserRail rejects as
# an empty answer.


def test_a_real_option_still_resolves_normally_when_other_is_present() -> None:
    assert resolve_reply("1", OPTIONS_WITH_OTHER) == ("Yes", "")
    assert resolve_reply("No", OPTIONS_WITH_OTHER) == ("No", "")


@pytest.mark.parametrize("text", ["3", "Other", "other", " OTHER "])
def test_bare_other_by_number_or_label_is_not_a_complete_answer(text: str) -> None:
    """Other exists to carry custom text; alone it must not resume the tool."""
    assert resolve_reply(text, OPTIONS_WITH_OTHER) is None


def test_free_text_that_matches_no_option_becomes_other_custom_input() -> None:
    assert resolve_reply("I'd like something else entirely", OPTIONS_WITH_OTHER) == (
        "Other",
        "I'd like something else entirely",
    )


def test_an_out_of_range_index_becomes_other_custom_input_when_other_is_offered() -> None:
    """A stray number is still free text once the question offers a custom path."""
    assert resolve_reply("99", OPTIONS_WITH_OTHER) == ("Other", "99")


def test_unmatched_text_without_other_still_falls_through() -> None:
    """No Other option means the pre-existing "let it through" behavior is unchanged."""
    assert resolve_reply("actually, wait", OPTIONS) is None


def test_resolve_reply_matches_match_reply_for_free_text_questions() -> None:
    assert resolve_reply("  use the second one  ", ()) == ("use the second one", "")


def test_resolve_reply_on_empty_text_is_not_an_answer() -> None:
    assert resolve_reply("   ", OPTIONS_WITH_OTHER) is None


# ---------------------------------------------------------------- registry


def _registry() -> PendingQuestionRegistry:
    return PendingQuestionRegistry()


def test_a_registered_question_resolves_its_reply() -> None:
    reg = _registry()
    reg.register(request_id="req-1", channel_id="telegram", conversation_key="chat-9", options=OPTIONS)

    resolved = reg.resolve("telegram", "chat-9", "2")

    assert resolved is not None
    question, answer, custom_input = resolved
    assert question.request_id == "req-1"
    assert answer == "Beta"
    assert custom_input == ""


def test_resolving_consumes_the_question() -> None:
    reg = _registry()
    reg.register(request_id="req-1", channel_id="telegram", conversation_key="chat-9", options=OPTIONS)

    assert reg.resolve("telegram", "chat-9", "1") is not None
    assert reg.resolve("telegram", "chat-9", "1") is None


def test_a_non_answer_does_not_consume_the_question() -> None:
    """A stray message must not eat the question the user has yet to answer."""
    reg = _registry()
    reg.register(request_id="req-1", channel_id="telegram", conversation_key="chat-9", options=OPTIONS)

    assert reg.resolve("telegram", "chat-9", "hold on") is None
    assert reg.resolve("telegram", "chat-9", "3") is not None


def test_questions_are_scoped_per_conversation() -> None:
    reg = _registry()
    reg.register(request_id="req-1", channel_id="telegram", conversation_key="chat-9", options=OPTIONS)

    assert reg.resolve("telegram", "other-chat", "1") is None
    assert reg.resolve("slack", "chat-9", "1") is None
    assert reg.resolve("telegram", "chat-9", "1") is not None


def test_a_second_question_replaces_the_first() -> None:
    """The agent can only block on one ask_user per conversation."""
    reg = _registry()
    reg.register(request_id="req-1", channel_id="telegram", conversation_key="chat-9", options=OPTIONS)
    reg.register(request_id="req-2", channel_id="telegram", conversation_key="chat-9", options=("Yes", "No"))

    resolved = reg.resolve("telegram", "chat-9", "1")
    assert resolved is not None
    assert resolved[0].request_id == "req-2"
    assert resolved[1] == "Yes"


def test_an_expired_question_stops_shadowing_ordinary_messages() -> None:
    reg = PendingQuestionRegistry(ttl=-1)
    reg.register(request_id="req-1", channel_id="telegram", conversation_key="chat-9", options=OPTIONS)

    assert reg.peek("telegram", "chat-9") is None
    assert reg.resolve("telegram", "chat-9", "1") is None


def test_cleanup_reports_what_it_dropped() -> None:
    reg = PendingQuestionRegistry(ttl=-1)
    reg.register(request_id="req-1", channel_id="telegram", conversation_key="a", options=OPTIONS)
    reg.register(request_id="req-2", channel_id="telegram", conversation_key="b", options=OPTIONS)

    assert reg.cleanup_expired() == 2


def test_discard_removes_without_an_answer() -> None:
    reg = _registry()
    reg.register(request_id="req-1", channel_id="telegram", conversation_key="chat-9", options=OPTIONS)
    reg.discard("telegram", "chat-9")
    assert reg.peek("telegram", "chat-9") is None


def test_bare_other_reply_leaves_the_question_pending() -> None:
    """A bare Other selection is not a complete answer -- it must stay answerable."""
    reg = _registry()
    reg.register(request_id="req-1", channel_id="telegram", conversation_key="chat-9", options=OPTIONS_WITH_OTHER)

    assert reg.resolve("telegram", "chat-9", "Other") is None
    assert reg.peek("telegram", "chat-9") is not None


def test_custom_text_reply_resolves_with_other_and_consumes_the_question() -> None:
    reg = _registry()
    reg.register(request_id="req-1", channel_id="telegram", conversation_key="chat-9", options=OPTIONS_WITH_OTHER)

    resolved = reg.resolve("telegram", "chat-9", "Maybe both, actually")

    assert resolved is not None
    question, answer, custom_input = resolved
    assert question.request_id == "req-1"
    assert answer == "Other"
    assert custom_input == "Maybe both, actually"
    assert reg.peek("telegram", "chat-9") is None


# ------------------------------------------------------------- answer shape


def test_the_answer_is_shaped_like_an_interrupt_resume() -> None:
    """CLI/Web resume via chat.send + source=ask_user_interrupt, not chat.user_answer."""
    params = build_interrupt_resume_params("req-1", "Beta", question="Which one?")

    assert params["query"] == ""
    assert params["request_id"] == "req-1"
    assert params["source"] == "ask_user_interrupt"
    assert params["answers"][0]["selected_options"] == ["Beta"]
    assert params["answers"][0]["question"] == "Which one?"
    assert "custom_input" not in params["answers"][0]


def test_custom_input_travels_alongside_the_other_selection() -> None:
    """Matches the shape StructuredAskUserRail expects to recover free text (#2330)."""
    params = build_interrupt_resume_params(
        "req-1", "Other", question="Which one?", custom_input="Something else",
    )

    assert params["answers"][0]["selected_options"] == ["Other"]
    assert params["answers"][0]["custom_input"] == "Something else"


def test_register_keeps_the_interrupt_source() -> None:
    reg = _registry()
    reg.register(
        request_id="req-1",
        channel_id="telegram",
        conversation_key="chat-9",
        options=OPTIONS,
        source="ask_user_interrupt",
        question="Which one?",
    )
    pending = reg.peek("telegram", "chat-9")
    assert pending is not None
    assert pending.source == "ask_user_interrupt"
    assert pending.question == "Which one?"
