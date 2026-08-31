# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Gateway REST 身份头 → Agent 顶层 user_id + metadata.routing 重建。"""

from __future__ import annotations

from types import SimpleNamespace

from jiuwenswarm.common.request_identity import web_routing_identity
from jiuwenswarm.server.agent_http_routes import request_context
from jiuwenswarm.server.agent_http_server import build_agent_request


def test_build_agent_request_restores_metadata_routing() -> None:
    routing = {
        "user_id": "user1",
        "group_id": "__none__",
        "bot_id": "d64efe50-3b44-4895-b040-df922e1df242",
        "gateway_id": "4e3a795a-2339-4efd-895f-bc796943f57c",
    }
    req = build_agent_request(
        method="command.goal",
        params={"session_id": "s1", "action": "get"},
        request_id="r1",
        session_id="s1",
        channel_id="web",
        user_id="user1",
        is_stream=False,
        routing=routing,
    )
    assert req.metadata.get("user_id") == "user1"
    assert web_routing_identity(req.metadata) == routing
    assert req.metadata.get("routing") == {
        "group_id": "__none__",
        "bot_id": "d64efe50-3b44-4895-b040-df922e1df242",
        "gateway_id": "4e3a795a-2339-4efd-895f-bc796943f57c",
    }
    assert "bot_id" not in (req.params or {})


def test_request_context_reads_routing_headers() -> None:
    request = SimpleNamespace(
        headers={
            "x-request-id": "r1",
            "x-channel-id": "web",
            "x-session-id": "s1",
            "x-user-id": "user1",
            "x-group-id": "__none__",
            "x-bot-id": "bot-1",
            "x-gateway-id": "gw-1",
        },
        path_params={},
    )
    request_id, channel_id, session_id, user_id, routing = request_context(request)
    assert request_id == "r1"
    assert channel_id == "web"
    assert session_id == "s1"
    assert user_id == "user1"
    assert routing == {
        "user_id": "user1",
        "group_id": "__none__",
        "bot_id": "bot-1",
        "gateway_id": "gw-1",
    }
