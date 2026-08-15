# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for TeamManager.steer_leader against a real DeliverResult.

Every other test of this path mocks ``get_team_manager``, so the method itself
never runs. That matters more than it sounds: ``steer_leader`` decides accepted
vs rejected with ``if not result``, which works only because ``DeliverResult``
defines ``__bool__`` returning ``ok``. Nothing at the call site says so. A future
result type without that dunder -- or with a truthy failure -- would report every
rejection as success, and a steer a rail removed would be shown to the user as
applied.

So these tests patch only ``Runner.steer_agent_team``, the seam *below* the code
under test, and hand it genuine ``DeliverResult`` values. That is the difference
between testing the contract and testing a mock of it, and this branch has already
paid twice for the second kind.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.agent_teams.interaction.payload import DeliverResult

from jiuwenswarm.agents.harness.team.team_manager import TeamManager


def _manager(*, active_team: str | None = "alpha") -> TeamManager:
    """A TeamManager with only the session -> team mapping steer_leader reads."""
    manager = TeamManager.__new__(TeamManager)
    manager._active_team_names = {"sess-1": active_team} if active_team else {}
    manager._pending_team_names = {}
    return manager


def _runner(result: DeliverResult) -> MagicMock:
    runner = MagicMock()
    runner.steer_agent_team = AsyncMock(return_value=result)
    return runner


@pytest.mark.anyio
async def test_a_real_success_result_is_reported_as_accepted() -> None:
    runner = _runner(DeliverResult.success(None))
    with patch("jiuwenswarm.agents.harness.team.team_manager.Runner", runner):
        ok, reason = await _manager().steer_leader(
            "sess-1", "prefer the async client", steer_id="req-9"
        )

    assert ok is True
    assert reason is None
    # The arguments matter as much as the outcome: team_name is resolved from the
    # session, and steer_id is what lets chat.steer_applied name a dropped steer.
    runner.steer_agent_team.assert_awaited_once_with(
        "prefer the async client",
        team_name="alpha",
        session_id="sess-1",
        steer_id="req-9",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "token",
    [
        "no_active_round",
        "not_active",
        "gate_closed",
        "missing_target",
        "unsupported_runtime",
    ],
)
async def test_a_real_failure_result_passes_its_token_through(token: str) -> None:
    """Each token tells the user something different, so none may be collapsed."""
    runner = _runner(DeliverResult.failure(token))
    with patch("jiuwenswarm.agents.harness.team.team_manager.Runner", runner):
        ok, reason = await _manager().steer_leader("sess-1", "hi")

    assert ok is False
    assert reason == token


@pytest.mark.anyio
async def test_the_falsiness_of_a_failure_result_is_what_drives_the_branch() -> None:
    """The coupling this file exists for.

    ``steer_leader`` branches on ``if not result``, which is only correct because
    ``DeliverResult.__bool__`` returns ``ok``. Asserted here directly so that
    removing or changing that dunder fails a test naming the reason, rather than
    silently turning every rejection into a success.
    """
    assert bool(DeliverResult.success(None)) is True
    assert bool(DeliverResult.failure("no_active_round")) is False


@pytest.mark.anyio
async def test_a_failure_without_a_reason_still_yields_a_stable_token() -> None:
    """A result type that fails without saying why must not surface reason=None."""
    runner = _runner(DeliverResult(ok=False))
    with patch("jiuwenswarm.agents.harness.team.team_manager.Runner", runner):
        ok, reason = await _manager().steer_leader("sess-1", "hi")

    assert ok is False
    assert reason == "runner_failed"


@pytest.mark.anyio
async def test_a_session_with_no_active_team_never_reaches_the_runner() -> None:
    runner = _runner(DeliverResult.success(None))
    with patch("jiuwenswarm.agents.harness.team.team_manager.Runner", runner):
        ok, reason = await _manager(active_team=None).steer_leader("sess-1", "hi")

    assert ok is False
    assert reason == "not_active"
    runner.steer_agent_team.assert_not_awaited()


@pytest.mark.anyio
async def test_a_raising_runner_becomes_a_reason_not_a_crash() -> None:
    """steer_leader mirrors interact's contract: it returns, it does not raise.

    The caller turns this tuple into an ACK, so an escaping exception would reach
    the client as a transport error rather than an answered request.
    """
    runner = MagicMock()
    runner.steer_agent_team = AsyncMock(side_effect=RuntimeError("boom"))
    with patch("jiuwenswarm.agents.harness.team.team_manager.Runner", runner):
        ok, reason = await _manager().steer_leader("sess-1", "hi")

    assert ok is False
    assert reason == "exception"


@pytest.mark.anyio
async def test_missing_steer_api_is_unsupported_runtime_not_exception() -> None:
    """A structural absence must not read as a transient server fault.

    When Runner.steer_agent_team is missing, AttributeError is raised. Mapping
    that to ``exception`` makes the TUI say "server errored" and invite retries
    of something that can never work. ``unsupported_runtime`` already exists
    for exactly this case.
    """
    runner = MagicMock(spec=[])  # no steer_agent_team attribute
    with patch("jiuwenswarm.agents.harness.team.team_manager.Runner", runner):
        ok, reason = await _manager().steer_leader("sess-1", "hi")

    assert ok is False
    assert reason == "unsupported_runtime"


@pytest.mark.anyio
async def test_steer_id_is_optional_and_forwarded_as_none() -> None:
    """A caller with no request id must not be turned into a positional error."""
    runner = _runner(DeliverResult.success(None))
    with patch("jiuwenswarm.agents.harness.team.team_manager.Runner", runner):
        ok, _ = await _manager().steer_leader("sess-1", "hi")

    assert ok is True
    assert runner.steer_agent_team.await_args.kwargs["steer_id"] is None
