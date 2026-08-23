from __future__ import annotations

import pytest

from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm


def test_permission_answer_uses_only_opaque_card_id() -> None:
    interactive = JiuWenSwarm._build_interactive_input_from_answers(
        "batch-call-1",
        [
            {
                "selected_options": ["本次允许"],
                "card_id": " batch-invocation-1 ",
            },
        ],
        source="permission_interrupt",
    )

    assert interactive.user_inputs == {
        "batch-invocation-1": {
            "approved": True,
            "auto_confirm": False,
            "feedback": "",
        },
    }


def test_permission_multi_answer_fails_closed() -> None:
    interactive = JiuWenSwarm._build_interactive_input_from_answers(
        "batch-call-1",
        [
            {
                "selected_options": ["本次允许"],
                "card_id": "batch-invocation-1",
            },
            {
                "selected_options": ["拒绝"],
                "card_id": "batch-invocation-2",
            },
        ],
        source="permission_interrupt",
    )

    assert interactive.user_inputs == {}


@pytest.mark.parametrize(
    "answers",
    [
        [
            {
                "selected_options": ["本次允许"],
                "card_id": "batch-invocation-1",
            },
            {
                "selected_options": ["本次允许"],
                "card_id": "batch-invocation-2",
            },
        ],
        [
            {
                "selected_options": ["本次允许"],
                "card_id": "",
            }
        ],
        [
            {
                "selected_options": ["本次允许"],
                "tool_invocation_id": "batch-invocation-1",
            }
        ],
    ],
)
def test_malformed_permission_card_answers_produce_no_resume_input(
    answers: list[dict],
) -> None:
    interactive = JiuWenSwarm._build_interactive_input_from_answers(
        "batch-call-1",
        answers,
        source="permission_interrupt",
    )

    assert interactive.user_inputs == {}


def test_confirm_interrupt_keeps_its_independent_request_protocol() -> None:
    interactive = JiuWenSwarm._build_interactive_input_from_answers(
        "confirm-call",
        [{"selected_options": ["本次允许"]}],
        source="confirm_interrupt",
    )

    assert interactive.user_inputs == {
        "confirm-call": {
            "approved": True,
            "auto_confirm": False,
            "feedback": "",
        }
    }


@pytest.mark.parametrize("source", ["", "unknown"])
def test_missing_or_unknown_source_does_not_build_approval_input(source: str) -> None:
    result = JiuWenSwarm._build_interactive_input_from_answers(
        "call-1",
        [{"selected_options": ["本次允许"]}],
        source=source,
    )

    assert result is None


def test_non_permission_answer_does_not_forward_locator() -> None:
    interactive = JiuWenSwarm._build_interactive_input_from_answers(
        "question-1",
        [
            {
                "question": "Continue?",
                "selected_options": ["Yes"],
                "tool_invocation_id": "invocation-1",
            }
        ],
        source="ask_user_interrupt",
    )

    assert interactive.user_inputs == {"question-1": {"answers": {"Continue?": "Yes"}}}


@pytest.mark.parametrize(
    ("request_id", "answers"),
    [
        ("", [{"question": "Continue?", "selected_options": ["Yes"]}]),
        ("question-1", [{"selected_options": ["Yes"]}]),
    ],
)
def test_ask_user_requires_exact_call_and_question_keys(
    request_id: str,
    answers: list[dict],
) -> None:
    interactive = JiuWenSwarm._build_interactive_input_from_answers(
        request_id,
        answers,
        source="ask_user_interrupt",
    )

    assert interactive.raw_inputs is None
    assert interactive.user_inputs == {}


def test_ask_user_does_not_accept_original_request_legacy_argument() -> None:
    with pytest.raises(TypeError):
        JiuWenSwarm._build_interactive_input_from_answers(
            "question-1",
            [{"question": "Continue?", "selected_options": ["Yes"]}],
            source="ask_user_interrupt",
            original_request="legacy authorization text",
        )
