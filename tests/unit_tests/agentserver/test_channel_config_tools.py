import json
import sys
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.channel_runtime_context import (
    CURRENT_CHANNEL_ID,
    CURRENT_SESSION_ID,
)
from jiuwenswarm.agents.harness.common.tools.channel_config_tools import (
    _gateway_control_pipe_credentials,
    _request_gateway_control,
)
from jiuwenswarm.common import np_transport, secrets_bootstrap
from jiuwenswarm.common.channel_config_registry import (
    CONFIGURABLE_THIRD_PARTY_CHANNEL_IDS,
    is_configurable_third_party_channel,
)


class _FakePipeStream:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_frame(self, frame: dict) -> None:
        self.sent.append(frame)

    async def recv_frame(self, timeout: float | None = None) -> dict:
        if len(self.sent) == 1:
            return {"type": "event", "event": "connection.ack", "payload": {}}
        request = self.sent[-1]
        if request["method"] == "channel.configure":
            payload = {
                "channel_id": request["params"]["channel_id"],
                "config": {"enabled": True},
            }
        else:
            payload = {"channel_id": "wechat", "login": {"phase": "idle"}}
        return {
            "type": "res",
            "id": request["id"],
            "ok": True,
            "payload": payload,
        }

    async def close(self) -> None:
        self.closed = True


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self._ack_sent = False

    async def recv(self) -> str:
        if not self._ack_sent:
            self._ack_sent = True
            return json.dumps({"type": "event", "event": "connection.ack", "payload": {}})
        request = self.sent[-1]
        return json.dumps({
            "type": "res",
            "id": request["id"],
            "ok": True,
            "payload": {"channel_id": "wechat", "login": {"phase": "idle"}},
        })

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
    stream = _FakePipeStream()
    secrets = {
        "e2aToken": "e2a-test-token",
        "pipes.cron": r"\\.\pipe\claw-cron",
    }
    monkeypatch.setattr(
        secrets_bootstrap,
        "get_secret",
        lambda key, default=None: secrets.get(key, default),
    )

    async def _open_pipe(path: str, **_kwargs: object) -> _FakePipeStream:
        assert path == r"\\.\pipe\claw-cron"
        return stream

    monkeypatch.setattr(np_transport, "open_pipe", _open_pipe)

    result = await _request_gateway_control(
        "channel.configure",
        {"channel_id": "feishu", "settings": {"enabled": True, "app_id": "cli_test"}},
    )

    assert result["config"]["enabled"] is True
    assert stream.sent[1]["method"] == "channel.configure"
    assert stream.sent[1]["params"] == {
        "channel_id": "feishu",
        "settings": {"enabled": True, "app_id": "cli_test"},
    }


@pytest.mark.asyncio
async def test_request_gateway_control_uses_cron_pipe_only(
    monkeypatch: pytest.MonkeyPatch,
):
    stream = _FakePipeStream()
    opened_paths: list[str] = []
    secrets = {
        "e2aToken": "e2a-test-token",
        "pipes.cron": r"\\.\pipe\claw-cron",
    }
    monkeypatch.setattr(
        secrets_bootstrap,
        "get_secret",
        lambda key, default=None: secrets.get(key, default),
    )

    async def _open_pipe(path: str, **_kwargs: object) -> _FakePipeStream:
        opened_paths.append(path)
        return stream

    monkeypatch.setattr(np_transport, "open_pipe", _open_pipe)

    result = await _request_gateway_control("wechat.login_status", {})

    assert result["login"]["phase"] == "idle"
    assert opened_paths == [r"\\.\pipe\claw-cron"]
    assert stream.sent[0] == {"type": "auth", "token": "e2a-test-token"}
    assert stream.sent[1]["method"] == "wechat.login_status"
    assert stream.closed is True


@pytest.mark.asyncio
async def test_request_gateway_control_falls_back_to_websocket_without_pipe_credentials(
    monkeypatch: pytest.MonkeyPatch,
):
    ws = _FakeWebSocket()
    monkeypatch.setattr(
        secrets_bootstrap,
        "get_secret",
        lambda _key, default=None: default,
    )
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        SimpleNamespace(connect=lambda *args, **kwargs: _FakeConnect(ws)),
    )

    result = await _request_gateway_control("wechat.login_status", {})

    assert result["login"]["phase"] == "idle"
    assert ws.sent[0]["method"] == "wechat.login_status"


def test_gateway_control_pipe_credentials_require_cron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = {
        "e2aToken": "e2a-test-token",
        "pipes.cron": r"\\.\pipe\claw-cron",
    }
    monkeypatch.setattr(
        secrets_bootstrap,
        "get_secret",
        lambda key, default=None: secrets.get(key, default),
    )

    assert _gateway_control_pipe_credentials() == (
        r"\\.\pipe\claw-cron",
        "e2a-test-token",
    )


@pytest.mark.parametrize(
    "secrets",
    (
        {"e2aToken": "e2a-test-token"},
        {"pipes.cron": r"\\.\pipe\claw-cron"},
    ),
)
def test_gateway_control_pipe_credentials_reject_incomplete_exe_secrets(
    monkeypatch: pytest.MonkeyPatch,
    secrets: dict[str, str],
) -> None:
    monkeypatch.setattr(
        secrets_bootstrap,
        "get_secret",
        lambda key, default=None: secrets.get(key, default),
    )

    with pytest.raises(RuntimeError, match="named pipe is not configured"):
        _gateway_control_pipe_credentials()


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
    stream = _FakePipeStream()
    secrets = {"e2aToken": "t", "pipes.cron": r"\\.\pipe\claw-cron"}
    monkeypatch.setattr(
        secrets_bootstrap, "get_secret", lambda key, default=None: secrets.get(key, default)
    )

    async def _open_pipe(*_args, **_kwargs):
        return stream

    monkeypatch.setattr(np_transport, "open_pipe", _open_pipe)

    result = await _request_gateway_control(
        "channel.configure",
        {"channel_id": "wecom", "settings": {"enabled": True, "corp_id": "corp_test"}},
    )

    assert result["channel_id"] == "wecom"
    assert stream.sent[1]["method"] == "channel.configure"
    assert stream.sent[1]["params"] == {
        "channel_id": "wecom",
        "settings": {"enabled": True, "corp_id": "corp_test"},
    }


@pytest.mark.asyncio
async def test_gateway_control_includes_requester_channel_and_session(monkeypatch: pytest.MonkeyPatch):
    stream = _FakePipeStream()
    secrets = {"e2aToken": "t", "pipes.cron": r"\\.\pipe\claw-cron"}
    monkeypatch.setattr(
        secrets_bootstrap, "get_secret", lambda key, default=None: secrets.get(key, default)
    )

    async def _open_pipe(*_args, **_kwargs):
        return stream

    monkeypatch.setattr(np_transport, "open_pipe", _open_pipe)
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

    assert stream.sent[1]["params"]["requester"] == {
        "channel_id": "qq",
        "session_id": "qq_c2c:user",
    }
