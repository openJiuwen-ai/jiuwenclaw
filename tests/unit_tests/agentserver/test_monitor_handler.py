# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest
from openjiuwen.agent_teams.monitor.models import MonitorEvent, MonitorEventType

from jiuwenswarm.agents.harness.team.monitor_handler import TeamMonitorHandler


class _FakeMember:
    def __init__(self, member_name: str, display_name: str = "", status: str = "ready",
                 execution_status: str | None = None, mode: str = "normal"):
        self.member_name = member_name
        self.display_name = display_name
        self.status = status
        self.execution_status = execution_status
        self.mode = mode


class _FakeMonitor:
    def __init__(
        self,
        members: list[_FakeMember],
        leader_member_name: str | None,
        events: list[MonitorEvent] | None = None,
    ):
        self.team_id = "team-1"
        self._members = members
        self._leader_member_name = leader_member_name
        self._events = events or []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def events(self):
        for event in self._events:
            yield event

    async def get_members(self) -> list[_FakeMember]:
        return list(self._members)

    async def get_team_info(self):
        if self._leader_member_name is None:
            return None
        return SimpleNamespace(leader_member_name=self._leader_member_name)


@pytest.mark.anyio
async def test_get_team_snapshot_filters_leader_member() -> None:
    handler = TeamMonitorHandler(
        _FakeMonitor(
            members=[_FakeMember("team_leader"), _FakeMember("worker-1")],
            leader_member_name="team_leader",
        ),
        "sess-1",
    )

    snapshot = await handler.get_team_snapshot()

    assert snapshot == {
        "members": [
            {
                "member_id": "worker-1",
                "name": "",
                "status": "ready",
                "execution_status": None,
                "mode": "normal",
            }
        ],
        "team_id": "team-1",
    }


@pytest.mark.anyio
async def test_get_team_snapshot_keeps_members_when_team_info_unavailable() -> None:
    handler = TeamMonitorHandler(
        _FakeMonitor(
            members=[_FakeMember("worker-1"), _FakeMember("worker-2")],
            leader_member_name=None,
        ),
        "sess-2",
    )

    snapshot = await handler.get_team_snapshot()

    assert snapshot == {
        "members": [
            {
                "member_id": "worker-1",
                "name": "",
                "status": "ready",
                "execution_status": None,
                "mode": "normal",
            },
            {
                "member_id": "worker-2",
                "name": "",
                "status": "ready",
                "execution_status": None,
                "mode": "normal",
            },
        ],
        "team_id": "team-1",
    }


@pytest.mark.anyio
async def test_convert_event_includes_session_id() -> None:
    event = MonitorEvent(
        event_type=MonitorEventType.TASK_CREATED,
        team_name="team-1",
        timestamp=123,
        task_id="task-1",
        status="created",
    )
    handler = TeamMonitorHandler(
        _FakeMonitor(
            members=[],
            leader_member_name=None,
            events=[event],
        ),
        "sess-monitor",
    )

    await handler.start()
    try:
        converted = await anext(handler.events())

        assert converted == {
            "event_type": "team.task",
            "session_id": "sess-monitor",
            "event": {
                "type": "team.task.created",
                "team_id": "team-1",
                "task_id": "task-1",
                "status": "created",
            },
        }
    finally:
        await handler.stop()
