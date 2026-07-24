"""Process-local guard for Team runtime startup.

The current agent-core release creates and migrates per-session SQLite tables
while the first item is pulled from ``Runner.run_agent_team_streaming``. The
database layer does not expose a lifecycle lock, so JiuwenSwarm serializes
only that first pull. Once initialization has produced the first stream item,
Team rounds are allowed to run concurrently as before.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from openjiuwen.core.runner import Runner


_TEAM_STARTUP_LOCK = asyncio.Lock()


async def iter_team_stream_with_startup_guard(
    *,
    agent_team: Any,
    inputs: dict[str, Any],
    session: str,
    envs: dict[str, Any] | None,
    stream_logger: Any | None,
    runner: Any | None = None,
) -> AsyncIterator[Any]:
    """Yield a Team stream while serializing only its initialization pull.

    ``Runner.run_agent_team_streaming`` is an async generator. In the current
    runtime, session binding and dynamic-table migration happen before the
    first item is yielded. Holding the process-local lock across that first
    ``anext`` prevents two local sessions from migrating the shared SQLite
    file simultaneously, without serializing the rest of their streams.
    """
    runner_impl = Runner if runner is None else runner
    stream: AsyncIterator[Any] | None = None
    try:
        stream = runner_impl.run_agent_team_streaming(
            agent_team=agent_team,
            inputs=inputs,
            session=session,
            envs=envs,
            stream_logger=stream_logger,
        )
        async with _TEAM_STARTUP_LOCK:
            try:
                first_chunk = await anext(stream)
            except StopAsyncIteration:
                return

        yield first_chunk
        async for chunk in stream:
            yield chunk
    finally:
        if stream is not None:
            close = getattr(stream, "aclose", None)
            if callable(close):
                await close()
