# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest
from openjiuwen.agent_teams.monitor.models import MonitorEvent, MonitorEventType

from jiuwenswarm.agents.harness.team.handlers.team_monitor_handler import TeamMonitorHandler


class _FakeMessage:
    def __init__(
        self,
        message_id: str,
        content: str,
        protocol: str = "plain",
    ) -> None:
        self.message_id = message_id
        self.content = content
        self.protocol = protocol


class _FakeMember:
    def __init__(self, member_name: str, display_name: str = "", status: str = "ready",
                 execution_status: str | None = None, mode: str = "normal"):
        self.member_name = member_name
        self.display_name = display_name
        self.status = status
        self.execution_status = execution_status
        self.mode = mode


class _FakeTask:
    def __init__(self, task_id: str = "task-1", title: str = "test task",
                 content: str = "do something", status: str = "created",
                 assignee: str | None = None, updated_at: int | None = None):
        self.task_id = task_id
        self.team_name = "team-1"
        self.title = title
        self.content = content
        self.status = status
        self.assignee = assignee
        self.updated_at = updated_at


class _FakeMonitor:
    def __init__(
        self,
        members: list[_FakeMember],
        leader_member_name: str | None,
        events: list[MonitorEvent] | None = None,
        tasks: list[_FakeTask] | None = None,
        messages: list[_FakeMessage] | None = None,
    ):
        self.team_name = "team-1"
        self._members = members
        self._leader_member_name = leader_member_name
        self._events = events or []
        self._tasks = tasks or []
        self._messages = messages or []

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

    async def get_tasks(self) -> list[_FakeTask]:
        return list(self._tasks)

    async def get_messages(self) -> list[_FakeMessage]:
        return list(self._messages)


@pytest.mark.anyio
async def test_get_team_snapshot_filters_leader_member() -> None:
    handler = TeamMonitorHandler(
        _FakeMonitor(
            members=[_FakeMember("team_leader"), _FakeMember("worker-1")],
            leader_member_name="team_leader",
            tasks=[_FakeTask(task_id="task-1", title="research", status="created", assignee="worker-1")],
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
        "tasks": [
            {
                "task_id": "task-1",
                "team_name": "team-1",
                "title": "research",
                "content": "do something",
                "status": "created",
                "assignee": "worker-1",
                "updated_at": None,
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
        "tasks": [],
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


@pytest.mark.anyio
async def test_convert_json_protocol_message_decodes_unicode_escapes() -> None:
    event = MonitorEvent(
        event_type=MonitorEventType.MESSAGE,
        team_name="team-1",
        timestamp=123,
        message_id="msg-approval",
        from_member_name="team_leader",
        to_member_name="worker-1",
    )
    handler = TeamMonitorHandler(
        _FakeMonitor(
            members=[],
            leader_member_name=None,
            events=[event],
            messages=[
                _FakeMessage(
                    message_id="msg-approval",
                    content=(
                        '{"type": "tool_approval_result", "approved": true, '
                        '"feedback": "\\u597d\\u8bd7"}'
                    ),
                    protocol="json",
                ),
            ],
        ),
        "sess-monitor",
    )

    await handler.start()
    try:
        converted = await anext(handler.events())

        assert converted["event"]["protocol"] == "json"
        assert converted["event"]["content"] == (
            '{"type": "tool_approval_result", "approved": true, "feedback": "好诗"}'
        )
    finally:
        await handler.stop()


@pytest.mark.anyio
async def test_convert_plain_protocol_message_keeps_json_like_text_unchanged() -> None:
    raw_content = '{"feedback": "\\u597d\\u8bd7"}'
    event = MonitorEvent(
        event_type=MonitorEventType.BROADCAST,
        team_name="team-1",
        timestamp=123,
        message_id="msg-plain",
        from_member_name="team_leader",
    )
    handler = TeamMonitorHandler(
        _FakeMonitor(
            members=[],
            leader_member_name=None,
            events=[event],
            messages=[
                _FakeMessage(
                    message_id="msg-plain",
                    content=raw_content,
                    protocol="plain",
                ),
            ],
        ),
        "sess-monitor",
    )

    await handler.start()
    try:
        converted = await anext(handler.events())

        assert converted["event"]["protocol"] == "plain"
        assert converted["event"]["content"] == raw_content
    finally:
        await handler.stop()
