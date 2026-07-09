from __future__ import annotations

from jiuwenswarm.common.device_rpc.models import (
    DeviceCommandContext,
    DeviceCommandRequest,
)
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi import (
    xiaoyi_connect as connect_module,
)
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_connect import (
    DataEvent,
    XiaoyiChannel,
    XiaoyiChannelConfig,
)
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_utils import (
    push as push_module,
)
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_utils.push import (
    PushConfig,
    XiaoYiPushService,
)

import pytest


def _scheduled_request() -> DeviceCommandRequest:
    return DeviceCommandRequest(
        rpc_id="rpc-1",
        operation_id="op-1",
        intent_name="CreateNote",
        command={"payload": {"executeParam": {"intentName": "CreateNote"}}},
        context=DeviceCommandContext(
            source_request_id="cron-run-1",
            channel_id="__cron__",
            jiuwen_session_id="cron-session",
            xiaoyi_root_session_id=None,
            xiaoyi_params_session_id=None,
            xiaoyi_task_id=None,
            xiaoyi_rpc_id=None,
            metadata={
                "scheduled_device": {
                    "push_id": "push-1",
                    "required_intents": ["CreateNote"],
                }
            },
        ),
        timeout_seconds=1.0,
    )


@pytest.mark.asyncio
async def test_push_with_directives_uses_openclaw_wire(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return "{}"

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr(push_module.aiohttp, "ClientSession", FakeSession)
    service = XiaoYiPushService(
        PushConfig(
            mode="xiaoyi_claw",
            api_id="api-1",
            push_id="push-1",
            uid="uid-1",
            api_key="key-1",
        )
    )

    sent = await service.send_push_with_directives(
        push_id="push-1",
        session_id="session-1",
        directives=[{"intent": "CreateNote"}],
    )

    result = captured["json"]["result"]
    assert sent is True
    assert result["pushType"] == 101
    assert result["pushText"] == ""
    assert result["pushId"] == "push-1"
    assert result["sessionId"] == "session-1"
    assert result["artifacts"][0]["parts"][0] == {
        "kind": "data",
        "data": {"directives": [{"intent": "CreateNote"}]},
    }


@pytest.mark.asyncio
async def test_scheduled_command_round_trip_via_directive_push(monkeypatch) -> None:
    channel = XiaoyiChannel(
        XiaoyiChannelConfig(
            mode="xiaoyi_claw",
            api_id="api-1",
            uid="uid-1",
            api_key="key-1",
        ),
        object(),
    )
    calls = []

    async def fake_send(self, push_id, session_id, directives):
        calls.append((push_id, session_id, directives))
        await channel._handle_data_event(
            DataEvent(
                intent_name="CreateNote",
                outputs={"code": 0},
                status="success",
            )
        )
        return True

    monkeypatch.setattr(
        connect_module.XiaoYiPushService,
        "send_push_with_directives",
        fake_send,
    )

    result = await channel.execute_scheduled_phone_tool_command(
        _scheduled_request()
    )

    assert result == {"code": 0}
    assert calls[0][0] == "push-1"
    assert calls[0][2] == [_scheduled_request().command]
    assert channel._data_event_handlers["CreateNote"] == []


@pytest.mark.asyncio
async def test_privilege_check_returns_outputs_for_denied_status(monkeypatch) -> None:
    channel = XiaoyiChannel(XiaoyiChannelConfig(), object())
    context = DeviceCommandContext(
        source_request_id="request-1",
        channel_id="xiaoyi",
        jiuwen_session_id="jiuwen-session",
        xiaoyi_root_session_id="xiaoyi-session",
        xiaoyi_params_session_id=None,
        xiaoyi_task_id="task-1",
        xiaoyi_rpc_id="message-1",
        metadata={},
    )
    request = DeviceCommandRequest(
        rpc_id="rpc-1",
        operation_id="op-1",
        intent_name="CheckPlugInPrivilege",
        command={},
        context=context,
        timeout_seconds=1.0,
    )

    async def fake_send(**kwargs):
        assert kwargs["message_id"] == "message-1"
        await channel._handle_data_event(
            DataEvent(
                intent_name="CheckPlugInPrivilege",
                outputs={"authorized": False},
                status="failed",
            )
        )
        return True

    monkeypatch.setattr(channel, "send_xiaoyi_phone_tools_command", fake_send)

    result = await channel.execute_phone_tool_command(request)

    assert result == {"authorized": False}


@pytest.mark.asyncio
async def test_scheduled_command_rejects_empty_scheduled_intents(monkeypatch) -> None:
    channel = XiaoyiChannel(
        XiaoyiChannelConfig(
            mode="xiaoyi_claw",
            api_id="api-1",
            push_id="fallback-push",
            uid="uid-1",
            api_key="key-1",
        ),
        object(),
    )
    original = _scheduled_request()
    request = DeviceCommandRequest(
        rpc_id=original.rpc_id,
        operation_id=original.operation_id,
        intent_name=original.intent_name,
        command=original.command,
        context=DeviceCommandContext(
            source_request_id=original.context.source_request_id,
            channel_id="__cron__",
            jiuwen_session_id=original.context.jiuwen_session_id,
            xiaoyi_root_session_id=None,
            xiaoyi_params_session_id=None,
            xiaoyi_task_id=None,
            xiaoyi_rpc_id=None,
            metadata={
                "cron": {"job_id": "job-1", "run_id": "run-1"},
                "scheduled_device": {
                    "push_id": "fallback-push",
                    "required_intents": [],
                },
            },
        ),
        timeout_seconds=1.0,
    )
    async def fake_send(self, push_id, session_id, directives):
        raise AssertionError("directive push must not run")

    monkeypatch.setattr(
        connect_module.XiaoYiPushService,
        "send_push_with_directives",
        fake_send,
    )

    with pytest.raises(RuntimeError, match="recreate the cron job"):
        await channel.execute_scheduled_phone_tool_command(request)
