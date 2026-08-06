# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team stream pricing helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.team import get_team_manager
from jiuwenswarm.common.usage_cost import new_usage_accumulator
from jiuwenswarm.server.runtime.agent_adapter import team_helpers

_CFG = {
    "models": {
        "pricing": {
            "m-leader": {"input": 1.0, "output": 1.0},
            "m-reviewer": {"input": 2.0, "output": 2.0},
        }
    }
}


@pytest.fixture(autouse=True)
def _pricing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jiuwenswarm.common.usage_cost.get_config", lambda: _CFG)


def _chunk(model: str, member: str, *, inp: int, out: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        type="llm_usage",
        source_member=member,
        role="teammate" if member != "leader" else "leader",
        payload={
            "usage_metadata": {
                "model_name": model,
                "input_tokens": inp,
                "output_tokens": out,
                "total_tokens": inp + out,
                "cache_tokens": 0,
                "input_cost": 0.0,
                "output_cost": 0.0,
                "total_cost": 0.0,
            }
        },
    )


def test_team_llm_usage_fills_by_member() -> None:
    acc = new_usage_accumulator()
    payload = team_helpers._record_team_llm_usage(
        _chunk("m-leader", "leader", inp=1_000_000),
        acc,
        is_leader=True,
    )
    assert payload is not None
    assert payload["member_name"] == "leader"
    assert payload["event_type"] == "chat.usage_metadata"

    team_helpers._record_team_llm_usage(
        _chunk("m-reviewer", "reviewer", inp=1_000_000),
        acc,
        is_leader=False,
    )
    team_helpers._record_team_llm_usage(
        _chunk("local-model", "reviewer", inp=1_000_000),
        acc,
        is_leader=False,
    )

    assert acc["by_member"]["leader"]["priced_calls"] == 1
    assert acc["by_member"]["reviewer"]["priced_calls"] == 1
    assert acc["by_member"]["reviewer"]["unpriced_calls"] == 1
    assert acc["by_member"]["leader"]["total_cost"] == pytest.approx(1.0)
    assert acc["by_member"]["reviewer"]["total_cost"] == pytest.approx(2.0)


def test_broadcast_team_usage_summary_is_awaited() -> None:
    """``_broadcast_team_usage_summary`` must actually run ``_broadcast_event``.

    Regression test for the bug where the helper called ``_broadcast_event``
    (an ``async def``) without ``await``, so the coroutine was created but
    its body never executed and ``chat.usage_summary`` was never delivered.
    A bare (unawaited) call would leave ``sent`` empty here; only an
    ``async def`` helper that is itself awaited by the caller drives the
    broadcast coroutine to completion.
    """
    acc = new_usage_accumulator()
    team_helpers._record_team_llm_usage(
        _chunk("m-leader", "leader", inp=1_000_000),
        acc,
        is_leader=True,
    )

    sent: list[dict] = []

    async def _fake_broadcast_event(channel_id, session_id, event):
        sent.append(event)

    async def _drive() -> None:
        # If _broadcast_team_usage_summary is a plain ``def`` (the bug),
        # awaiting its return value (None) raises TypeError immediately.
        await team_helpers._broadcast_team_usage_summary(
            None, "sess-1", acc, round_id=1
        )

    import unittest.mock

    with unittest.mock.patch.object(
        team_helpers, "_broadcast_event", _fake_broadcast_event
    ):
        asyncio.run(_drive())

    assert len(sent) == 1
    assert sent[0]["event_type"] == "chat.usage_summary"
    assert sent[0]["rid"] == 1


def test_consume_stream_broadcasts_llm_usage_and_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: llm_usage chunks and the round summary must reach clients.

    Drives the real ``_consume_stream_with_query`` helper (using the real,
    global ``TeamManager`` broadcast/queue machinery) with a stubbed team
    runner stream that yields a single leader ``llm_usage`` chunk. Regression
    test for both call sites that used to call the async
    ``_broadcast_event``/``_broadcast_team_usage_summary`` without awaiting
    them, silently dropping ``chat.usage_metadata`` and ``chat.usage_summary``.
    """
    session_id = "test-b7-session-usage"
    request_id = "test-b7-request"
    channel_id = None

    tm = get_team_manager(channel_id)
    queue: asyncio.Queue = asyncio.Queue()
    tm.add_waiter(session_id, request_id, queue)

    def _fake_run_agent_team_streaming(**kwargs):
        async def _gen():
            yield _chunk("m-leader", "leader", inp=1_000_000)

        return _gen()

    monkeypatch.setattr(
        team_helpers.Runner,
        "run_agent_team_streaming",
        _fake_run_agent_team_streaming,
    )

    team_spec = SimpleNamespace(team_name="test-team", enable_swarmflow=False)

    try:
        asyncio.run(
            team_helpers._consume_stream_with_query(
                channel_id,
                session_id,
                team_spec,
                "hello",
                round_id=1,
                envs={},
            )
        )

        received: list[dict] = []
        while not queue.empty():
            received.append(queue.get_nowait())
    finally:
        tm.remove_waiter(session_id, request_id)
        tm.pop_stream_task(session_id)

    event_types = [e.get("event_type") for e in received]
    assert "chat.usage_metadata" in event_types, event_types
    assert "chat.usage_summary" in event_types, event_types
