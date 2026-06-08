from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest

from jiuwenclaw.channel import vibeskill_channel as module
from jiuwenclaw.channel.vibeskill_channel import VibeSkillChannel, VibeSkillConfig


class FakeRouter:
    def __init__(self) -> None:
        self.delivered: list[Any] = []

    def deliver_to_message_handler(self, msg: Any) -> None:
        self.delivered.append(msg)


class FakeAgentClient:
    def is_openability_reconnecting(
        self,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> bool:
        return False


class FakeRequest:
    method = "GET"
    path_qs = "/api/v1/session/sid-http/messages?x=1"
    headers: dict[str, str] = {}
    path = "/api/v1/messages"
    query_string = ""

    async def read(self) -> bytes:
        return b""


class FakeWsRequest:
    def __init__(self, query_string: str = "") -> None:
        self.path = "/api/v1/messages"
        self.query_string = query_string
        self.headers: dict[str, str] = {}


class FakeWebSocket:
    def __init__(self, messages: list[str] | None = None, close_code: int | None = 1000) -> None:
        self.messages = list(messages or [])
        self.sent: list[dict[str, Any]] = []
        self.request = FakeWsRequest()
        self.remote_address = "127.0.0.1"
        self.closed = False
        self.close_code = close_code
        self.close_reason: str | None = None

    def __aiter__(self) -> FakeWebSocket:
        return self

    async def __anext__(self) -> str:
        if not self.messages:
            self.closed = True
            raise StopAsyncIteration
        return self.messages.pop(0)

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason


@pytest.fixture
def interface_log_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "interface.log"
    monkeypatch.setenv("INTERFACE_LOG_PATH", str(path))
    module._configure_interface_log_path()
    yield path
    monkeypatch.delenv("INTERFACE_LOG_PATH", raising=False)
    module._configure_interface_log_path()


@pytest.fixture
def channel(monkeypatch: pytest.MonkeyPatch) -> VibeSkillChannel:
    monkeypatch.delenv("SANDBOX_DCS_HOST", raising=False)
    monkeypatch.setattr(
        VibeSkillChannel,
        "_get_local_ip",
        staticmethod(lambda: "127.0.0.1"),
    )
    return VibeSkillChannel(
        config=VibeSkillConfig(),
        router=FakeRouter(),
        agent_client=FakeAgentClient(),
    )


def _flush_interface_logger() -> None:
    for handler in module.interface_logger.handlers:
        handler.flush()


def _read_rows(path: Path) -> list[list[str]]:
    _flush_interface_logger()
    return [line.split("|") for line in path.read_text(encoding="utf-8").splitlines()]


def test_interface_log_uses_fixed_pipe_delimited_fields(interface_log_path: Path) -> None:
    module._log_interface_event(
        severity="INFO",
        session_id="sid|with\nbreak",
        interface_type="HTTP",
        http_url="GET /api/v1/session/sid\twith|pipe",
        response_time_ms=12,
        return_code=200,
    )

    rows = _read_rows(interface_log_path)
    assert len(rows) == 1
    row = rows[0]
    assert len(row) == 9
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}", row[0])
    assert row[1:] == [
        "INFO",
        "sid with break",
        "HTTP",
        "GET /api/v1/session/sid with pipe",
        "",
        "12",
        "200",
        "",
    ]


@pytest.mark.asyncio
async def test_aiohttp_http_handler_logs_success_status_and_session(
    channel: VibeSkillChannel,
    interface_log_path: Path,
) -> None:
    async def fake_http_handler(
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        return 200, {"Content-Type": "application/json"}, b'{"sessionID":"sid-created"}'

    request = FakeRequest()
    request.method = "POST"
    request.path_qs = "/api/v1/session"
    channel.http_handler = fake_http_handler  # type: ignore[method-assign]

    await channel._aiohttp_http_handler(request)

    row = _read_rows(interface_log_path)[0]
    assert len(row) == 9
    assert row[1] == "INFO"
    assert row[2] == "sid-created"
    assert row[3] == "HTTP"
    assert row[4] == "POST /api/v1/session"
    assert row[5] == ""
    assert row[6].isdigit()
    assert row[7] == "200"
    assert row[8] == ""


@pytest.mark.asyncio
async def test_aiohttp_http_handler_logs_response_sent_to_main_logger(
    channel: VibeSkillChannel,
    interface_log_path: Path,
) -> None:
    async def fake_http_handler(
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        return 200, {"Content-Type": "application/json"}, b"{}"

    request = FakeRequest()
    channel.http_handler = fake_http_handler  # type: ignore[method-assign]

    captured: list[logging.LogRecord] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = _CaptureHandler()
    module.logger.addHandler(handler)
    try:
        await channel._aiohttp_http_handler(request)
    finally:
        module.logger.removeHandler(handler)

    messages = [
        r.getMessage()
        for r in captured
        if "[VibeSkillChannel] HTTP 响应已发送" in r.getMessage()
    ]
    assert len(messages) == 1
    assert "status=200" in messages[0]
    assert "session_id=sid-http" in messages[0]
    assert "path=/api/v1/session/sid-http/messages?x=1" in messages[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "severity"),
    [(404, "WARN"), (500, "ERROR")],
)
async def test_aiohttp_http_handler_logs_error_severity_by_status(
    channel: VibeSkillChannel,
    interface_log_path: Path,
    status: int,
    severity: str,
) -> None:
    async def fake_http_handler(
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        return status, {"Content-Type": "application/json"}, b"{}"

    request = FakeRequest()
    channel.http_handler = fake_http_handler  # type: ignore[method-assign]

    await channel._aiohttp_http_handler(request)

    row = _read_rows(interface_log_path)[0]
    assert len(row) == 9
    assert row[1] == severity
    assert row[2] == "sid-http"
    assert row[4] == "GET /api/v1/session/sid-http/messages?x=1"
    assert row[7] == str(status)


@pytest.mark.asyncio
async def test_aiohttp_messages_without_upgrade_logs_http_426(
    channel: VibeSkillChannel,
    interface_log_path: Path,
) -> None:
    request = FakeRequest()
    request.method = "GET"
    request.path = "/api/v1/messages"
    request.path_qs = "/api/v1/messages"
    request.headers = {}

    response = await channel._aiohttp_ws_handler(request)

    row = _read_rows(interface_log_path)[0]
    assert response.status == 426
    assert len(row) == 9
    assert row[1] == "WARN"
    assert row[3] == "HTTP"
    assert row[4] == "GET /api/v1/messages"
    assert row[7] == "426"


@pytest.mark.asyncio
async def test_websocket_logs_inbound_and_close(
    channel: VibeSkillChannel,
    interface_log_path: Path,
) -> None:
    session = await channel._store.get_or_create(session_id="sid-ws", mode="SkillCreate")
    payload = {
        "type": "message.send",
        "id": "req-1",
        "sessionID": session.session_id,
        "parts": [{"type": "text", "text": "build a skill"}],
    }
    ws = FakeWebSocket([json.dumps(payload)])

    await channel._handle_ws_connection(ws)

    rows = _read_rows(interface_log_path)
    assert all(len(row) == 9 for row in rows)
    inbound = [row for row in rows if row[5] == "message.send"]
    assert inbound
    assert inbound[-1][1] == "INFO"
    assert inbound[-1][2] == "sid-ws"
    assert inbound[-1][3] == "WebSocket"
    assert inbound[-1][6] == ""
    close = [row for row in rows if row[5] == "close"][-1]
    assert close[1] == "INFO"
    assert close[2] == "sid-ws"
    assert close[6] == ""
    assert close[8] == "true"


@pytest.mark.asyncio
async def test_websocket_logs_invalid_json_as_warn(
    channel: VibeSkillChannel,
    interface_log_path: Path,
) -> None:
    ws = FakeWebSocket(["not-json"])

    await channel._handle_ws_connection(ws)

    rows = _read_rows(interface_log_path)
    invalid = [row for row in rows if row[5] == "invalid_json"]
    assert invalid
    assert invalid[-1][1] == "WARN"
    assert invalid[-1][3] == "WebSocket"
    assert invalid[-1][6] == ""


@pytest.mark.asyncio
async def test_send_ws_json_does_not_log_outbound_event(
    channel: VibeSkillChannel,
    interface_log_path: Path,
) -> None:
    ws = FakeWebSocket()

    await channel._send_ws_json(
        ws,
        {"type": "message.updated", "properties": {"sessionID": "sid-out"}},
        source="test",
    )

    assert _read_rows(interface_log_path) == []


def test_interface_log_path_does_not_capture_business_logger(
    interface_log_path: Path,
) -> None:
    module.logger.info("[VibeSkillChannel] business log should not enter interface file")
    module._log_interface_event(severity="INFO", interface_type="HTTP", http_url="GET /ok")

    text = interface_log_path.read_text(encoding="utf-8")
    assert "[VibeSkillChannel]" not in text
    assert "vibeskill_channel.py" not in text
    assert "GET /ok" in text
