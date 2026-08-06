"""Tests for the JiuwenSwarm Team startup compatibility guard."""

import asyncio

import pytest

from jiuwenswarm.server.runtime.agent_adapter import team_startup_guard


@pytest.mark.asyncio
async def test_team_startup_is_serialized_but_stream_body_remains_concurrent(monkeypatch):
    active_startups = 0
    max_active_startups = 0
    active_stream_bodies = 0
    max_active_stream_bodies = 0

    async def fake_stream(**kwargs):
        nonlocal active_startups, max_active_startups
        nonlocal active_stream_bodies, max_active_stream_bodies

        active_startups += 1
        max_active_startups = max(max_active_startups, active_startups)
        await asyncio.sleep(0.01)
        active_startups -= 1
        yield f"first:{kwargs['session']}"

        active_stream_bodies += 1
        max_active_stream_bodies = max(max_active_stream_bodies, active_stream_bodies)
        await asyncio.sleep(0.02)
        active_stream_bodies -= 1
        yield f"second:{kwargs['session']}"

    monkeypatch.setattr(
        team_startup_guard.Runner,
        "run_agent_team_streaming",
        staticmethod(fake_stream),
    )

    async def consume(session: str) -> list[str]:
        return [
            item
            async for item in team_startup_guard.iter_team_stream_with_startup_guard(
                agent_team=object(),
                inputs={"query": "hello"},
                session=session,
                envs=None,
                stream_logger=None,
            )
        ]

    results = await asyncio.gather(consume("session-a"), consume("session-b"))

    assert results == [["first:session-a", "second:session-a"], ["first:session-b", "second:session-b"]]
    assert max_active_startups == 1
    assert max_active_stream_bodies == 2


@pytest.mark.asyncio
async def test_stream_creation_error_is_not_masked_by_cleanup(monkeypatch):
    def failing_stream(**kwargs):
        raise RuntimeError("startup stream creation failed")

    monkeypatch.setattr(
        team_startup_guard.Runner,
        "run_agent_team_streaming",
        staticmethod(failing_stream),
    )

    with pytest.raises(RuntimeError, match="startup stream creation failed"):
        [
            item
            async for item in team_startup_guard.iter_team_stream_with_startup_guard(
                agent_team=object(),
                inputs={"query": "hello"},
                session="session-error",
                envs=None,
                stream_logger=None,
            )
        ]
