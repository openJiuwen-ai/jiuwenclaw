# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for stamp_plan_pause_onto_live_session (plan pause lost-update guard)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.deep_agent import plan_pause_helpers as helpers
from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import (
    PLAN_PAUSED_SESSION_KEY,
    stamp_plan_pause_onto_live_session,
)


@pytest.mark.asyncio
async def test_stamp_plan_pause_onto_live_session_noop_without_loop() -> None:
    instance = SimpleNamespace(loop_session=None)
    stamped = await stamp_plan_pause_onto_live_session(
        instance,
        "sess-1",
        snapshot={"todos": []},
    )
    assert stamped is False


@pytest.mark.asyncio
async def test_stamp_plan_pause_onto_live_session_noop_on_session_id_mismatch() -> None:
    live = MagicMock()
    live.get_session_id = MagicMock(return_value="other-sess")
    instance = SimpleNamespace(loop_session=live, load_state=MagicMock(), save_state=MagicMock())

    stamped = await stamp_plan_pause_onto_live_session(
        instance,
        "sess-1",
        snapshot=None,
    )
    assert stamped is False
    instance.load_state.assert_not_called()


@pytest.mark.asyncio
async def test_stamp_plan_pause_onto_live_session_writes_flag_and_flushes() -> None:
    live = MagicMock()
    live.get_session_id = MagicMock(return_value="sess-1")
    live.update_state = MagicMock()
    live.post_run = AsyncMock()

    state = MagicMock(name="deep_state")
    instance = SimpleNamespace(
        loop_session=live,
        load_state=MagicMock(return_value=state),
        save_state=MagicMock(),
    )
    checkpointer = MagicMock(name="cp")

    with (
        patch.object(helpers, "repair_task_plan_after_pause", return_value=True) as repair,
        patch.object(helpers, "post_agent_execute_for_session", new_callable=AsyncMock) as flush,
    ):
        stamped = await stamp_plan_pause_onto_live_session(
            instance,
            "sess-1",
            snapshot={"items": [1]},
            checkpointer=checkpointer,
        )

    assert stamped is True
    repair.assert_called_once_with(state)
    instance.save_state.assert_called_once_with(live, state)
    live.update_state.assert_called_once()
    updates = live.update_state.call_args[0][0]
    assert updates[PLAN_PAUSED_SESSION_KEY] is True
    assert updates[helpers.PLAN_PAUSED_SNAPSHOT_KEY] == {"items": [1]}
    flush.assert_awaited_once_with(live, checkpointer)
    live.post_run.assert_not_called()
