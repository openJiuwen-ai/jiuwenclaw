# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.server.runtime.agent_adapter import evolution_helpers


def test_evolution_helpers_parse_approval_and_outcome_events():
    approval = SimpleNamespace(
        type="chat.ask_user_question",
        payload={"request_id": "team_skill_evolve_req1", "questions": [{"header": "x"}]},
    )
    outcome = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {"event_kind": "outcome", "status": "completed"},
            "content": "done",
        },
    )

    assert evolution_helpers.is_evolution_approval_event(approval) is True
    assert evolution_helpers.evolution_event_kind(outcome) == "outcome"
    assert evolution_helpers.is_evolution_outcome_event(outcome) is True
    assert evolution_helpers.evolution_outcome_from_event(outcome) == {
        "status": "completed",
        "message": "done",
    }
    assert evolution_helpers.extract_evolution_request_id(approval) == "team_skill_evolve_req1"


@pytest.mark.parametrize("stage", ["failed", "timed_out"])
def test_evolution_helpers_hide_failed_and_timed_out_team_status_updates(stage: str):
    update = evolution_helpers.team_evolution_end_update(
        "team_skill_evolve_req1",
        {"stage": stage, "message": "boom"},
    )

    assert update.request_id == "team_skill_evolve_req1"
    assert update.status == "end"
    assert update.stage == "hidden"
    assert update.message == "boom"


def test_evolution_helpers_map_noop_progress_to_no_evolution_generated():
    progress = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {"event_kind": "progress", "stage": "completed"},
            "content": "No evolution signals detected",
        },
    )

    terminal = evolution_helpers.team_evolution_terminal_progress(progress)
    update = evolution_helpers.team_evolution_end_update(
        "team_skill_evolve_req1",
        terminal,
    )

    assert terminal == {
        "status": "completed",
        "stage": "no_evolution_generated",
        "message": "No evolution signals detected",
    }
    assert update.status == "end"
    assert update.stage == "no_evolution_generated"
    assert update.message == "No evolution signals detected"


def test_evolution_helpers_group_approvals_skips_missing_request_ids():
    missing_request_id = SimpleNamespace(
        type="chat.ask_user_question",
        payload={"questions": [{"header": "missing"}]},
    )
    real_request_id = SimpleNamespace(
        type="chat.ask_user_question",
        payload={"request_id": "team_skill_evolve_real", "questions": [{"header": "real"}]},
    )
    skipped_stream = SimpleNamespace(
        type="llm_reasoning",
        payload={"content": "progress"},
    )
    warnings: list[str] = []

    grouped, missing_request_ids = evolution_helpers.group_evolution_approvals(
        "sess-1",
        [missing_request_id, skipped_stream, real_request_id],
        warn_missing_request_id=lambda session_id: warnings.append(session_id),
    )

    assert missing_request_ids == []
    assert warnings == ["sess-1"]
    assert list(grouped) == ["team_skill_evolve_real"]
    assert grouped["team_skill_evolve_real"] == [real_request_id]


def test_evolution_helpers_builds_team_cycle_request_id():
    assert (
        evolution_helpers.make_team_evolution_cycle_request_id("sess-1", 2)
        == "team_evolve_sess-1_2"
    )


@pytest.mark.asyncio
async def test_evolution_helpers_push_status_can_omit_payload_request_id():
    pushes: list[dict] = []

    class _Transport:
        @staticmethod
        async def send_push(payload: dict) -> None:
            pushes.append(payload)

    def _build_push_message(**kwargs):
        return kwargs

    await evolution_helpers.push_evolution_status(
        evolution_helpers.EvolutionPushContext(
            transport=_Transport(),
            channel_id="web",
            session_id="sess-1",
        ),
        evolution_helpers.EvolutionStatusUpdate(
            request_id="stream-rid",
            status="start",
            stage="collecting",
            message="started",
        ),
        _build_push_message,
        include_payload_request_id=False,
    )

    assert pushes == [
        {
            "session_id": "sess-1",
            "request_id": "stream-rid",
            "fallback_channel_id": "web",
            "payload": {
                "event_type": "chat.evolution_status",
                "status": "start",
                "stage": "collecting",
                "message": "started",
            },
        }
    ]


@pytest.mark.asyncio
async def test_evolution_helpers_broadcast_progress_skips_non_stream_evolution_events():
    approval = SimpleNamespace(
        type="chat.ask_user_question",
        payload={"request_id": "team_skill_evolve_req1"},
    )
    outcome = SimpleNamespace(
        type="chat.evolution_status",
        payload={"_evolution_meta": {"event_kind": "outcome"}, "message": "done"},
    )
    terminal = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {"event_kind": "progress", "stage": "completed"},
            "content": "No evolution signals detected",
        },
    )
    stream = SimpleNamespace(
        type="llm_reasoning",
        payload={"content": "thinking"},
    )
    broadcasts: list[tuple[str | None, str, dict]] = []

    await evolution_helpers.broadcast_evolution_progress(
        "web",
        "sess-1",
        [approval, outcome, terminal, stream],
        parse_stream_chunk=lambda evt: {
            "event_type": "chat.reasoning",
            "content": evt.payload["content"],
        },
        broadcast_event=lambda channel_id, session_id, payload: broadcasts.append(
            (channel_id, session_id, payload)
        ),
    )

    assert broadcasts == [
        (
            "web",
            "sess-1",
            {"event_type": "chat.reasoning", "content": "thinking"},
        )
    ]


@pytest.mark.asyncio
async def test_evolution_helpers_push_progress_skips_non_stream_evolution_events():
    approval = SimpleNamespace(
        type="chat.ask_user_question",
        payload={"request_id": "skill_evolve_req1"},
    )
    outcome = SimpleNamespace(
        type="chat.evolution_status",
        payload={"_evolution_meta": {"event_kind": "outcome"}, "message": "done"},
    )
    terminal = SimpleNamespace(
        type="llm_reasoning",
        payload={
            "_evolution_meta": {"event_kind": "progress", "stage": "completed"},
            "content": "No evolution signals detected",
        },
    )
    stream = SimpleNamespace(
        type="llm_reasoning",
        payload={"content": "thinking"},
    )
    ignored = SimpleNamespace(
        type="llm_reasoning",
        payload={"content": ""},
    )
    pushes: list[dict] = []

    class _Transport:
        @staticmethod
        async def send_push(payload: dict) -> None:
            pushes.append(payload)

    def _build_push_message(**kwargs):
        return kwargs

    await evolution_helpers.push_evolution_progress(
        evolution_helpers.EvolutionPushContext(
            transport=_Transport(),
            channel_id="web",
            session_id="sess-1",
        ),
        "stream-rid",
        [approval, outcome, terminal, stream, ignored],
        parse_stream_chunk=lambda evt: (
            None
            if not evt.payload.get("content")
            else {"event_type": "chat.reasoning", "content": evt.payload["content"]}
        ),
        build_push_message=_build_push_message,
    )

    assert pushes == [
        {
            "session_id": "sess-1",
            "request_id": "stream-rid",
            "fallback_channel_id": "web",
            "payload": {"event_type": "chat.reasoning", "content": "thinking"},
        }
    ]
