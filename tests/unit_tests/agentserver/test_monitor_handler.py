# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenclaw.agents.harness.team.monitor_handler import TeamMonitorHandler


class _FakeMember:
    def __init__(self, member_name: str):
        self.member_name = member_name

    def model_dump(self) -> dict[str, str]:
        return {"member_name": self.member_name}


class _FakeMonitor:
    def __init__(self, members: list[_FakeMember], leader_member_name: str | None):
        self.team_id = "team-1"
        self._members = members
        self._leader_member_name = leader_member_name

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
        "members": [{"member_name": "worker-1"}],
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
        "members": [{"member_name": "worker-1"}, {"member_name": "worker-2"}],
        "team_id": "team-1",
    }
