import json
import sys
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.tools.channel_config_tools import _request_gateway_control
from jiuwenswarm.common.channel_config_registry import (
    CONFIGURABLE_THIRD_PARTY_CHANNEL_IDS,
    is_configurable_third_party_channel,
)
from jiuwenswarm.agents.harness.common.channel_runtime_context import (
    CURRENT_CHANNEL_ID,
    CURRENT_SESSION_ID,
)


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._responses = [
            json.dumps({"type": "event", "event": "connection.ack", "payload": {}}),
            None,
        ]

    async def recv(self) -> str:
        response = self._responses.pop(0)
        if response is None:
            request_id = self.sent[-1]["id"]
            channel_id = self.sent[-1]["params"]["channel_id"]
            return json.dumps({
                "type": "res",
                "id": request_id,
                "ok": True,
                "payload": {"channel_id": channel_id, "config": {"enabled": True}},
            })
        return response

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


class _FakeConnect:
    def __init__(self, ws: _FakeWebSocket) -> None:
        self._ws = ws

    async def __aenter__(self) -> _FakeWebSocket:
        return self._ws

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


@pytest.mark.asyncio
async def test_request_gateway_control_sends_channel_configuration(monkeypatch: pytest.MonkeyPatch):
    ws = _FakeWebSocket()
    monkeypatch.setitem(sys.modules, "websockets", SimpleNamespace(connect=lambda *args, **kwargs: _FakeConnect(ws)))

    result = await _request_gateway_control(
        "channel.configure",
        {"channel_id": "feishu", "settings": {"enabled": True, "app_id": "cli_test"}},
    )

    assert result["config"]["enabled"] is True
    assert ws.sent[0]["method"] == "channel.configure"
    assert ws.sent[0]["params"] == {
        "channel_id": "feishu",
        "settings": {"enabled": True, "app_id": "cli_test"},
    }


def test_configurable_third_party_channels_include_supported_im_channels():
    assert CONFIGURABLE_THIRD_PARTY_CHANNEL_IDS == (
        "feishu",
        "feishu_enterprise",
        "xiaoyi",
        "dingtalk",
        "telegram",
        "discord",
        "whatsapp",
        "wecom",
        "wechat",
        "qq",
        "weibo",
    )
    for channel_id in CONFIGURABLE_THIRD_PARTY_CHANNEL_IDS:
        assert is_configurable_third_party_channel(channel_id)


@pytest.mark.asyncio
async def test_configure_channel_accepts_new_third_party_channels(monkeypatch: pytest.MonkeyPatch):
    ws = _FakeWebSocket()
    monkeypatch.setitem(sys.modules, "websockets", SimpleNamespace(connect=lambda *args, **kwargs: _FakeConnect(ws)))

    result = await _request_gateway_control(
        "channel.configure",
        {"channel_id": "wecom", "settings": {"enabled": True, "corp_id": "corp_test"}},
    )

    assert result["channel_id"] == "wecom"
    assert ws.sent[0]["method"] == "channel.configure"
    assert ws.sent[0]["params"] == {
        "channel_id": "wecom",
        "settings": {"enabled": True, "corp_id": "corp_test"},
    }


@pytest.mark.asyncio
async def test_gateway_control_includes_requester_channel_and_session(monkeypatch: pytest.MonkeyPatch):
    ws = _FakeWebSocket()
    monkeypatch.setitem(sys.modules, "websockets", SimpleNamespace(connect=lambda *args, **kwargs: _FakeConnect(ws)))
    channel_token = CURRENT_CHANNEL_ID.set("qq")
    session_token = CURRENT_SESSION_ID.set("qq_c2c:user")
    try:
        await _request_gateway_control(
            "channel.configure",
            {"channel_id": "wechat", "settings": {"enabled": True}},
        )
    finally:
        CURRENT_CHANNEL_ID.reset(channel_token)
        CURRENT_SESSION_ID.reset(session_token)

    assert ws.sent[0]["params"]["requester"] == {
        "channel_id": "qq",
        "session_id": "qq_c2c:user",
    }
