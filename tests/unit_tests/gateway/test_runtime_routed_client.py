# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""RuntimeRoutedAgentClient：route → HTTP base_url → touch。"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

import httpx

from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenswarm.common.schema.agent import AgentResponse, AgentResponseChunk
from jiuwenswarm.common.schema.message import ReqMethod

_EXT_DIR = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "jiuwenclaw-ee"
    / "gateway"
    / "extensions"
    / "runtime_management_extension"
)
_PKG = "_ee_runtime_management_ext"
if _PKG not in sys.modules:
    _pkg = types.ModuleType(_PKG)
    _pkg.__path__ = [str(_EXT_DIR)]
    _pkg.__package__ = _PKG
    sys.modules[_PKG] = _pkg


def _load(name: str):
    full = f"{_PKG}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, _EXT_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    module.__package__ = _PKG
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


_route_mod = _load("session_route_client")
_routed_mod = _load("runtime_routed_client")
FatalRouteError = _route_mod.FatalRouteError
RetryableRouteError = _route_mod.RetryableRouteError
RouteResult = _route_mod.RouteResult
RuntimeRoutedAgentClient = _routed_mod.RuntimeRoutedAgentClient
http_base_from_pod_sse_url = _routed_mod.http_base_from_pod_sse_url
identity_from_envelope = _routed_mod.identity_from_envelope


def _chat_env():
    return e2a_from_agent_fields(
        request_id="req-1",
        channel_id="web",
        session_id="sess-1",
        req_method=ReqMethod.CHAT_SEND,
        params={"query": "hi", "group_id": "grp-1", "bot_id": "bot-1"},
        is_stream=True,
        user_id="user-1",
    )


class _FakeRoute:
    def __init__(self) -> None:
        self.routes: list[dict] = []
        self.touches: list[dict] = []
        self.fail_times = 0

    async def route(self, **kwargs):
        self.routes.append(kwargs)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RetryableRouteError("full", code="SCOPE_QUEUE_FULL", retry_after=0)
        return RouteResult(
            pod_sse_url="http://10.1.2.3:8080/sse",
            pod_id="pod-1",
            request_id=kwargs["request_id"],
        )

    async def touch(self, **kwargs):
        self.touches.append(kwargs)
        return True

    async def aclose(self) -> None:
        return None


class _FakeHttp:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fail_first = False
        self.fail_exc: BaseException = httpx.ConnectError("http down")

    async def send_request(self, envelope, *, base_url=None):
        self.calls.append(("unary", base_url, envelope.request_id))
        if self.fail_first:
            self.fail_first = False
            raise self.fail_exc
        return AgentResponse(
            request_id=str(envelope.request_id),
            channel_id="web",
            ok=True,
            payload={"ok": True},
        )

    async def send_request_stream(self, envelope, *, base_url=None):
        self.calls.append(("stream", base_url, envelope.request_id))
        if self.fail_first:
            self.fail_first = False
            raise self.fail_exc
        yield AgentResponseChunk(
            request_id=str(envelope.request_id),
            channel_id="web",
            payload={"content": "hi"},
            is_complete=True,
        )

    async def disconnect(self) -> None:
        return None


def test_http_base_from_pod_sse_url() -> None:
    assert http_base_from_pod_sse_url("http://10.0.0.1:8080/sse") == "http://10.0.0.1:8080"
    with pytest.raises(FatalRouteError):
        http_base_from_pod_sse_url("not-a-url")


def test_identity_from_envelope() -> None:
    env = _chat_env()
    session_id, group_id, bot_id, request_id, user_id = identity_from_envelope(env)
    assert (session_id, group_id, bot_id, request_id, user_id) == (
        "sess-1",
        "grp-1",
        "bot-1",
        "req-1",
        "user-1",
    )


@pytest.mark.asyncio
async def test_unary_routes_then_http_then_touch() -> None:
    route = _FakeRoute()
    http = _FakeHttp()
    client = RuntimeRoutedAgentClient(
        route_client=route, http_client=http, touch_interval_seconds=999
    )
    await client.connect("")
    env = _chat_env()
    env.is_stream = False
    result = await client.send_request(env)
    assert result.ok is True
    assert route.routes[0]["session_id"] == "sess-1"
    assert http.calls[0] == ("unary", "http://10.1.2.3:8080", "req-1")
    assert route.touches
    await client.disconnect()


@pytest.mark.asyncio
async def test_stream_and_route_retry() -> None:
    route = _FakeRoute()
    route.fail_times = 1
    http = _FakeHttp()
    client = RuntimeRoutedAgentClient(
        route_client=route,
        http_client=http,
        touch_interval_seconds=999,
        route_attempts=2,
    )
    await client.connect("")
    chunks = [c async for c in client.send_request_stream(_chat_env())]
    assert len(chunks) == 1
    assert len(route.routes) == 2
    assert http.calls[0][0] == "stream"
    await client.disconnect()


@pytest.mark.asyncio
async def test_http_fail_before_chunk_reroutes_with_new_id() -> None:
    route = _FakeRoute()
    http = _FakeHttp()
    http.fail_first = True
    client = RuntimeRoutedAgentClient(
        route_client=route, http_client=http, touch_interval_seconds=999
    )
    await client.connect("")
    chunks = [c async for c in client.send_request_stream(_chat_env())]
    assert len(chunks) == 1
    assert len(route.routes) == 2
    assert route.routes[0]["request_id"] == "req-1"
    assert route.routes[1]["request_id"] != "req-1"
    await client.disconnect()


@pytest.mark.asyncio
async def test_unary_http_fail_reroutes_with_new_id() -> None:
    route = _FakeRoute()
    http = _FakeHttp()
    http.fail_first = True
    client = RuntimeRoutedAgentClient(
        route_client=route, http_client=http, touch_interval_seconds=999
    )
    await client.connect("")
    assert client.server_ready is True
    env = _chat_env()
    env.is_stream = False
    result = await client.send_request(env)
    assert result.ok is True
    assert len(route.routes) == 2
    assert route.routes[0]["request_id"] == "req-1"
    assert route.routes[1]["request_id"] != "req-1"
    await client.disconnect()
    assert client.server_ready is False


def test_identity_from_chat_id_query_and_agent_ref() -> None:
    env = e2a_from_agent_fields(
        request_id="req-2",
        channel_id="web",
        session_id="sess-2",
        req_method=ReqMethod.CHAT_SEND,
        params={"query": "hi"},
        user_id="user-2",
    )
    env.chat_id = "grp-chat"
    env.agent_ref = {"mode": "agent", "id": "bot-ref"}
    session_id, group_id, bot_id, request_id, user_id = identity_from_envelope(env)
    assert (session_id, group_id, bot_id, request_id, user_id) == (
        "sess-2",
        "grp-chat",
        "bot-ref",
        "req-2",
        "user-2",
    )

    env2 = e2a_from_agent_fields(
        request_id="req-3",
        channel_id="web",
        session_id="sess-3",
        req_method=ReqMethod.CHAT_SEND,
        params={"query": "hi"},
    )
    env2.channel_context = {"query": {"group_id": ["g-q"], "bot_id": ["b-q"]}}
    _, group_id, bot_id, _, _ = identity_from_envelope(env2)
    assert (group_id, bot_id) == ("g-q", "b-q")


def test_identity_fallback_session_id() -> None:
    env = e2a_from_agent_fields(
        request_id="req-4",
        channel_id="web",
        req_method=ReqMethod.SESSION_CREATE,
        params={"group_id": "grp-1", "bot_id": "bot-1"},
        user_id="user-1",
    )
    session_id, group_id, bot_id, _, user_id = identity_from_envelope(env)
    assert session_id == "grp-1:bot-1:user-1"
    assert (group_id, bot_id, user_id) == ("grp-1", "bot-1", "user-1")


@pytest.mark.asyncio
async def test_heartbeat_skips_route() -> None:
    route = _FakeRoute()
    http = _FakeHttp()
    client = RuntimeRoutedAgentClient(
        route_client=route, http_client=http, touch_interval_seconds=999
    )
    await client.connect("")
    env = e2a_from_agent_fields(
        request_id="heartbeat-abc",
        channel_id="web",
        session_id="heartbeat_1",
        params={"heartbeat": "tick", "run": {"kind": "heartbeat"}},
    )
    result = await client.send_request(env)
    assert result.ok is True
    assert route.routes == []
    assert http.calls == []
    await client.disconnect()


@pytest.mark.asyncio
async def test_unary_value_error_not_rerouted() -> None:
    route = _FakeRoute()
    http = _FakeHttp()
    http.fail_first = True
    http.fail_exc = ValueError("assemble failed")
    client = RuntimeRoutedAgentClient(
        route_client=route, http_client=http, touch_interval_seconds=999
    )
    await client.connect("")
    env = _chat_env()
    env.is_stream = False
    with pytest.raises(ValueError, match="assemble"):
        await client.send_request(env)
    assert len(route.routes) == 1
    await client.disconnect()
