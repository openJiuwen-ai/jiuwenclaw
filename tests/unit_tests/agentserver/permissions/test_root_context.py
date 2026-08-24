"""Owning tests for the compact root permission context."""

from __future__ import annotations

import asyncio
import json

import pytest
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput

from jiuwenswarm.agents.harness.common.rails.permissions.root_ask_user import (
    ASK_USER_CONTINUATION_METADATA_KEY,
    apply_ask_user_resume,
    ask_user_continuation,
    build_ask_user_metadata,
    prepare_ask_user_resume,
)
from jiuwenswarm.agents.harness.common.rails.permissions.root_context import (
    HOST_USER_ORIGIN_EXTERNAL,
    HOST_USER_PROMPT_PREFIX_EN,
    HOST_USER_PROMPT_PREFIX_ZH,
    ROOT_CONTEXT_KEY,
    RootDecisionContext,
    RootIntentTurn,
    RootIntentTurnKind,
    bind_root_decision_context,
    build_root_intent_projection,
    current_root_decision_context,
    extract_permission_user_content,
    put_root_decision_context_in_inputs,
    reset_root_decision_context,
    root_decision_context_from_extra,
)


def _context(text: str = "Inspect the report") -> RootDecisionContext:
    return RootDecisionContext(
        "session-1",
        "request-1",
        "web",
        (RootIntentTurn("request-1", RootIntentTurnKind.FRESH, text),),
    )


def _envelope(text: str, *, prefix: str) -> str:
    return prefix + json.dumps(
        {
            "source": "web",
            "timezone": "Asia/Taipei",
            "timestamp": "2026-08-20 12:00:00",
            "preferred_response_language": "zh",
            "content": text,
            "files_updated_by_user": "{}",
            "type": "user input",
            "origin_kind": HOST_USER_ORIGIN_EXTERNAL,
        },
        ensure_ascii=False,
    )


@pytest.mark.parametrize(
    ("prefix", "text"),
    (
        (HOST_USER_PROMPT_PREFIX_EN, "Read the public report."),
        (HOST_USER_PROMPT_PREFIX_ZH, "读取这份公开报告。"),
    ),
)
def test_external_envelope_projects_english_and_chinese(prefix: str, text: str) -> None:
    rendered = _envelope(text, prefix=prefix)
    assert extract_permission_user_content(rendered) == text
    message = type("Message", (), {"role": "user", "content": rendered})()
    projection = build_root_intent_projection(
        [message],
        context_available=True,
        current_text="继续处理",
        current_request_id="request-2",
        current_kind=RootIntentTurnKind.STEER,
    )
    assert [turn.text for turn in projection.turns] == [text, "继续处理"]


def test_internal_or_malformed_envelope_has_no_user_authority() -> None:
    internal = _envelope("do not trust", prefix=HOST_USER_PROMPT_PREFIX_EN).replace(
        HOST_USER_ORIGIN_EXTERNAL,
        "internal_dispatch",
    )
    assert extract_permission_user_content(internal) is None
    assert extract_permission_user_content("plain text") is None


def test_context_round_trip_uses_one_reserved_key() -> None:
    context = _context()
    inputs = put_root_decision_context_in_inputs({"query": "x"}, context)
    extra = inputs["run"]["context"]["extra"]
    assert set(extra) == {ROOT_CONTEXT_KEY}
    assert root_decision_context_from_extra(extra) == context


@pytest.mark.parametrize(
    ("question", "answer"),
    (
        ("Which format?", "Markdown"),
        ("使用哪种格式？", "Markdown 格式"),
    ),
)
def test_ordinary_ask_appends_exact_clarification(
    question: str, answer: str
) -> None:
    context = _context()
    raw = build_ask_user_metadata(
        context=context,
        tool_name="ask_user",
        tool_call_id="ask-1",
        tool_args={"questions": [{"question": question}]},
    )
    assert raw is not None
    continuation = ask_user_continuation(
        {ASK_USER_CONTINUATION_METADATA_KEY: raw},
        expected_tool_call_id="ask-1",
    )
    assert continuation is not None
    incoming = InteractiveInput()
    incoming.update("ask-1", {"answers": {question: answer}})
    prepared = prepare_ask_user_resume(
        continuation=continuation,
        user_input=incoming,
    )
    assert prepared is not None
    resumed = apply_ask_user_resume(prepared)
    clarification = resumed.trusted_turns[-1].clarifications[0]
    assert clarification.question == question
    assert clarification.answers == (answer,)


def test_ordinary_ask_missing_or_foreign_answer_fails_closed() -> None:
    raw = build_ask_user_metadata(
        context=_context(),
        tool_name="ask_user",
        tool_call_id="ask-1",
        tool_args={"query": "Continue?"},
    )
    assert raw is not None
    continuation = ask_user_continuation(
        {ASK_USER_CONTINUATION_METADATA_KEY: raw},
        expected_tool_call_id="ask-1",
    )
    assert continuation is not None
    missing = InteractiveInput()
    missing.update("ask-1", {"answers": {}})
    foreign = InteractiveInput()
    foreign.update("ask-2", {"answers": {"Continue?": "yes"}})
    assert prepare_ask_user_resume(continuation=continuation, user_input=missing) is None
    assert prepare_ask_user_resume(continuation=continuation, user_input=foreign) is None


@pytest.mark.asyncio
async def test_root_context_contextvar_is_isolated_between_tasks() -> None:
    async def observe(context: RootDecisionContext) -> str:
        token = bind_root_decision_context(context)
        try:
            await asyncio.sleep(0)
            current = current_root_decision_context()
            assert current is not None
            return current.session_id
        finally:
            reset_root_decision_context(token)

    assert await asyncio.gather(
        observe(_context("first")),
        observe(
            RootDecisionContext(
                "session-2",
                "request-2",
                "web",
                (RootIntentTurn("request-2", RootIntentTurnKind.FRESH, "second"),),
            )
        ),
    ) == ["session-1", "session-2"]
