# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json

import pytest

from jiuwenclaw.app_enterprise_web import CHAT_ACCEPT_METHODS, EnterpriseWebWsServer


def test_chat_accept_methods_cover_design_set() -> None:
    assert "chat.send" in CHAT_ACCEPT_METHODS
    assert "chat.resume" in CHAT_ACCEPT_METHODS


@pytest.mark.asyncio
async def test_res_routes_by_pending_request_id() -> None:
    ws_server = EnterpriseWebWsServer(host="127.0.0.1", port=0)
    sent: list[str] = []

    class _FakeBrowser:
        async def send(self, data: str) -> None:
            sent.append(data)

    browser = _FakeBrowser()
    conn_id = "conn-test"
    ws_server.register_browser_connection(conn_id, browser)
    ws_server.bind_uplink_response_route("cfg-1", conn_id)

    uplink_raw = json.dumps(
        {"type": "res", "id": "cfg-1", "ok": True, "payload": {"model": "x"}},
        ensure_ascii=False,
    )
    await ws_server.route_uplink_frame(uplink_raw)

    assert len(sent) == 1
    assert json.loads(sent[0])["payload"]["model"] == "x"
    assert not ws_server.has_pending_uplink_request("cfg-1")


@pytest.mark.asyncio
async def test_event_routes_by_session_id() -> None:
    ws_server = EnterpriseWebWsServer(host="127.0.0.1", port=0)
    sent: list[str] = []

    class _FakeBrowser:
        async def send(self, data: str) -> None:
            sent.append(data)

    conn_id = "c1"
    ws_server.register_browser_connection(conn_id, _FakeBrowser())
    ws_server.subscribe_conn_to_session(conn_id, "sess_a")

    frame = {
        "type": "event",
        "event": "chat.delta",
        "payload": {"session_id": "sess_a", "content": "hi"},
    }
    await ws_server.route_uplink_frame(json.dumps(frame, ensure_ascii=False))

    assert len(sent) == 1
    assert json.loads(sent[0])["event"] == "chat.delta"


@pytest.mark.asyncio
async def test_connection_ack_routes_by_route_conn_id() -> None:
    ws_server = EnterpriseWebWsServer(host="127.0.0.1", port=0)
    sent: list[str] = []

    class _FakeBrowser:
        async def send(self, data: str) -> None:
            sent.append(data)

    conn_id = "conn-2"
    ws_server.register_browser_connection(conn_id, _FakeBrowser())

    frame = {
        "type": "event",
        "event": "connection.ack",
        "request_id": "ack-sess_abc123",
        "payload": {
            "session_id": "sess_abc123",
            "mode": "BUILD",
            "tools": [],
            "protocol_version": "1.0",
            "_route_conn_id": conn_id,
        },
    }
    await ws_server.route_uplink_frame(json.dumps(frame, ensure_ascii=False))

    assert len(sent) == 1
    browser_frame = json.loads(sent[0])
    assert browser_frame["event"] == "connection.ack"
    assert browser_frame["payload"]["session_id"] == "sess_abc123"
    assert "_route_conn_id" not in browser_frame["payload"]
    assert ws_server.get_active_session(conn_id) == "sess_abc123"
    assert ws_server.session_includes_conn("sess_abc123", conn_id)


@pytest.mark.asyncio
async def test_request_connection_ack_skipped_without_uplink() -> None:
    ws_server = EnterpriseWebWsServer(host="127.0.0.1", port=0)
    sent: list[str] = []

    class _FakeGateway:
        async def send(self, data: str) -> None:
            sent.append(data)

    await ws_server.request_gateway_connection_ack("conn-1")
    assert sent == []


@pytest.mark.asyncio
async def test_request_connection_ack_sends_web_connection_ack() -> None:
    ws_server = EnterpriseWebWsServer(host="127.0.0.1", port=0)
    sent: list[str] = []

    class _FakeGateway:
        async def send(self, data: str) -> None:
            sent.append(data)

    ws_server.attach_gateway_uplink(_FakeGateway())
    await ws_server.request_gateway_connection_ack("conn-3")

    assert len(sent) == 1
    req = json.loads(sent[0])
    assert req["type"] == "req"
    assert req["method"] == "web.connection_ack"
    assert req["params"]["conn_id"] == "conn-3"
