from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.tools.xiaoyi_phone_tools.device_tool_planner import (
    DEVICE_TOOL_ROUTE_BY_NAME,
    NO_DEVICE_TOOL,
    CronDeviceToolPlanner,
    map_device_tool_names,
)


class _FakeModel:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def invoke(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return self.responses.pop(0)


def _tool_response(*names: str) -> SimpleNamespace:
    return SimpleNamespace(
        content="",
        tool_calls=[SimpleNamespace(name=name) for name in names],
    )


def _text_response(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=text, tool_calls=None)


@pytest.mark.asyncio
async def test_planner_returns_real_device_tool_calls_without_execution() -> None:
    model = _FakeModel([_tool_response("create_note")])
    planner = CronDeviceToolPlanner(model_factory=lambda: model)

    plan = await planner.plan(
        name="write note",
        description="把test写入备忘录",
        privilege_intents={"CreateNote"},
    )

    assert plan.tool_names == ("create_note",)
    assert plan.allowed_intents == ("CreateNote",)
    assert plan.privilege_intents == ("CreateNote",)
    schemas = model.calls[0]["tools"]
    assert {item["function"]["name"] for item in schemas} == set(
        DEVICE_TOOL_ROUTE_BY_NAME
    )
    assert "xiaoyi_gui_agent" not in {
        item["function"]["name"] for item in schemas
    }


@pytest.mark.asyncio
async def test_planner_returns_multiple_ordered_tools() -> None:
    model = _FakeModel(
        [_tool_response("search_contact", "send_sms", "send_sms")]
    )
    planner = CronDeviceToolPlanner(model_factory=lambda: model)

    plan = await planner.plan(
        name="message contact",
        description="找到张三并发短信",
        privilege_intents={"SearchContactLocal", "SendShortMessage"},
    )

    assert plan.tool_names == ("search_contact", "send_sms")
    assert plan.allowed_intents == (
        "SearchContactLocal",
        "SendShortMessage",
    )


@pytest.mark.asyncio
async def test_planner_accepts_no_device_tool_marker() -> None:
    model = _FakeModel([_text_response(NO_DEVICE_TOOL)])
    planner = CronDeviceToolPlanner(model_factory=lambda: model)

    plan = await planner.plan(
        name="daily report",
        description="生成每日工作总结",
        privilege_intents=set(),
    )

    assert plan.is_device_task is False
    assert plan.allowed_intents == ()


@pytest.mark.asyncio
async def test_planner_retries_unknown_tool_once() -> None:
    model = _FakeModel(
        [
            _tool_response("unknown_device_tool"),
            _tool_response("create_alarm"),
        ]
    )
    planner = CronDeviceToolPlanner(model_factory=lambda: model)

    plan = await planner.plan(
        name="alarm",
        description="创建九点闹钟",
        privilege_intents={"CreateAlarm"},
    )

    assert plan.tool_names == ("create_alarm",)
    assert len(model.calls) == 2


@pytest.mark.asyncio
async def test_planner_fails_after_two_invalid_responses() -> None:
    model = _FakeModel(
        [
            _text_response("I cannot decide"),
            _text_response("still invalid"),
        ]
    )
    planner = CronDeviceToolPlanner(model_factory=lambda: model)

    with pytest.raises(RuntimeError, match="planning failed"):
        await planner.plan(
            name="ambiguous",
            description="do something later",
            privilege_intents=set(),
        )

    assert len(model.calls) == 2


def test_route_registry_maps_privileged_and_non_privileged_tools() -> None:
    plan = map_device_tool_names(
        ["create_note", "upload_file", "add_collection"],
        privilege_intents={"CreateNote"},
    )

    assert plan.allowed_intents == (
        "CreateNote",
        "FileUploadForClaw",
        "AddCollection",
    )
    assert plan.privilege_intents == ("CreateNote",)


def test_route_registry_covers_all_current_device_tools() -> None:
    assert set(DEVICE_TOOL_ROUTE_BY_NAME) == {
        "get_user_location",
        "create_note",
        "search_notes",
        "modify_note",
        "create_calendar_event",
        "search_calendar_event",
        "search_contact",
        "search_photo_gallery",
        "upload_photo",
        "search_file",
        "upload_file",
        "call_phone",
        "send_sms",
        "search_message",
        "create_alarm",
        "search_alarms",
        "modify_alarm",
        "delete_alarm",
        "query_collection",
        "add_collection",
        "delete_collection",
        "save_media_to_gallery",
        "save_file_to_file_manager",
    }
