# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PCS WebSocket API routing and Graph streaming tests."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError

from jiuwenswarm.common.e2a import wire_codec
from jiuwenswarm.common.e2a.wire_codec import parse_agent_server_wire_unary
from jiuwenswarm.gateway.routing import agent_client
from jiuwenswarm.common.schema.agent import AgentResponse
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server import agent_ws_server as server_module
from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer
from jiuwenswarm.server.proactive_context import ws_handler
from jiuwenswarm.server.proactive_context.ws_handler import handle_pcs_request


def test_pcs_does_not_extend_generic_e2a_codec_or_gateway_client() -> None:
    assert not hasattr(wire_codec, "validate_pcs_e2a_request_dict")
    assert not hasattr(wire_codec, "encode_pcs_request_for_wire")
    assert not hasattr(wire_codec, "encode_pcs_response_for_wire")
    assert not hasattr(wire_codec, "encode_pcs_chunk_for_wire")
    assert not hasattr(agent_client, "_is_pcs_method")


def test_agent_server_delegates_pcs_requests_to_module_handler() -> None:
    assert callable(ws_handler.handle_pcs_request)
    assert not hasattr(AgentWebSocketServer, "_handle_pcs_request")


class _FakeHost:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.failure: BaseException | None = None
        self.graph: dict[str, object] = {
            "context_ready": True,
            "nodes": [],
            "edges": [],
        }

    def _record(self, name: str, value: object, result: object) -> object:
        if self.failure is not None:
            raise self.failure
        self.calls.append((name, value))
        return result

    async def get_status(self) -> dict[str, object]:
        return self._record("get_status", None, {"state": "RUNNING"})

    async def set_runtime_enabled(self, enabled: bool) -> dict[str, object]:
        return self._record(
            "set_runtime_enabled",
            enabled,
            {"enabled": enabled},
        )

    async def get_runtime_config(self) -> dict[str, object]:
        return self._record(
            "get_runtime_config",
            None,
            {"strategy_profile": "rules"},
        )

    async def patch_runtime_config(
        self,
        patch: dict[str, object],
    ) -> dict[str, object]:
        return self._record("patch_runtime_config", patch, dict(patch))

    async def select_model(self, model_index: int) -> dict[str, object]:
        if type(model_index) is not int or model_index < 0:
            raise ValueError("model_index must be a non-negative integer")
        return self._record(
            "select_model",
            model_index,
            {"model_index": model_index},
        )

    async def list_fetch_services(self) -> list[dict[str, object]]:
        return self._record(
            "list_fetch_services",
            None,
            [{"service_id": "github-main", "state": "STOPPED"}],
        )

    async def patch_fetch_service(
        self,
        service_id: str,
        patch: dict[str, object],
    ) -> dict[str, object]:
        return self._record(
            "patch_fetch_service",
            (service_id, patch),
            {"service_id": service_id, **patch},
        )

    async def set_fetching(
        self,
        *,
        enabled: bool,
        service_id: str | None = None,
    ) -> None:
        self._record("set_fetching", (enabled, service_id), None)

    async def run_fetch(
        self,
        *,
        service_id: str | None = None,
    ) -> dict[str, object]:
        return self._record(
            "run_fetch",
            service_id,
            {
                "state": "accepted",
                "service_ids": [service_id] if service_id else ["github-main"],
            },
        )

    async def get_fetch_run_status(
        self,
        service_id: str | None = None,
    ) -> dict[str, object]:
        return self._record(
            "get_fetch_run_status",
            service_id,
            {"service_id": service_id, "state": "RUNNING"},
        )

    async def authorize_provider(self, provider: str) -> dict[str, object]:
        return self._record(
            "authorize_provider",
            provider,
            {"provider": provider, "state": "ready"},
        )

    async def get_graph(self) -> dict[str, object]:
        return self._record("get_graph", None, self.graph)

    async def search_graph(self, query: str) -> dict[str, object]:
        return self._record("search_graph", query, {"results": []})

    async def get_graph_page(self, node_id: str) -> dict[str, object]:
        return self._record(
            "get_graph_page",
            node_id,
            {"node_id": node_id, "markdown": "# PCS\n"},
        )


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.open = True


@pytest.fixture
def capture_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _capture(ws: _FakeWebSocket, payload: dict[str, object]) -> bool:
        ws.sent.append(payload)
        return True

    monkeypatch.setattr(server_module, "send_wire_payload", _capture)
    monkeypatch.setattr(ws_handler, "send_wire_payload", _capture)


def _server() -> tuple[AgentWebSocketServer, _FakeHost]:
    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    host = _FakeHost()
    server._pcs_host = host
    return server, host


def _request(
    method: ReqMethod,
    params: dict[str, object] | None = None,
    *,
    is_stream: bool = False,
) -> AgentRequest:
    return AgentRequest(
        request_id="pcs-1",
        channel_id="web",
        req_method=method,
        params=params or {},
        is_stream=is_stream,
        agent_ref={"mode": "agent", "id": "agent-1"},
    )


def _canonical(
    method: str,
    params: dict[str, object] | None = None,
    *,
    is_stream: bool = False,
) -> str:
    return json.dumps(
        {
            "protocol_version": "1.0",
            "request_id": "pcs-wire-1",
            "channel": "web",
            "method": method,
            "params": params or {},
            "is_stream": is_stream,
            "agent_ref": {"mode": "agent", "id": "agent-1"},
        }
    )


PCS_HOST_CALLS = [
    (ReqMethod.PCS_RUNTIME_STATUS, {}, "get_status", None),
    (ReqMethod.PCS_RUNTIME_START, {}, "set_runtime_enabled", True),
    (ReqMethod.PCS_RUNTIME_STOP, {}, "set_runtime_enabled", False),
    (ReqMethod.PCS_RUNTIME_GET_CONFIG, {}, "get_runtime_config", None),
    (
        ReqMethod.PCS_RUNTIME_PATCH_CONFIG,
        {"patch": {"strategy_profile": "rules"}},
        "patch_runtime_config",
        {"strategy_profile": "rules"},
    ),
    (ReqMethod.PCS_RUNTIME_SELECT_MODEL, {"model_index": 0}, "select_model", 0),
    (ReqMethod.PCS_FETCH_LIST_SERVICES, {}, "list_fetch_services", None),
    (
        ReqMethod.PCS_FETCH_PATCH_SERVICE,
        {"service_id": "github-main", "patch": {"interval_seconds": 10_800}},
        "patch_fetch_service",
        ("github-main", {"interval_seconds": 10_800}),
    ),
    (
        ReqMethod.PCS_FETCH_START_SERVICE,
        {"service_id": " github-main "},
        "set_fetching",
        (True, "github-main"),
    ),
    (
        ReqMethod.PCS_FETCH_STOP_SERVICE,
        {"service_id": "github-main"},
        "set_fetching",
        (False, "github-main"),
    ),
    (ReqMethod.PCS_FETCH_START_SCHEDULER, {}, "set_fetching", (True, None)),
    (ReqMethod.PCS_FETCH_STOP_SCHEDULER, {}, "set_fetching", (False, None)),
    (ReqMethod.PCS_FETCH_RUN_ALL, {}, "run_fetch", None),
    (
        ReqMethod.PCS_FETCH_RUN_ONE,
        {"service_id": "github-main"},
        "run_fetch",
        "github-main",
    ),
    (
        ReqMethod.PCS_FETCH_GET_RUN_STATUS,
        {"service_id": "github-main"},
        "get_fetch_run_status",
        "github-main",
    ),
    (
        ReqMethod.PCS_FETCH_AUTHORIZE_PROVIDER,
        {"provider": "feishu"},
        "authorize_provider",
        "feishu",
    ),
    (
        ReqMethod.PCS_CONTEXT_SEARCH_PAGES,
        {"query": "  主动上下文  "},
        "search_graph",
        "主动上下文",
    ),
    (
        ReqMethod.PCS_CONTEXT_GET_NODE,
        {"node_id": "page:topics/pcs.md"},
        "get_graph_page",
        "page:topics/pcs.md",
    ),
]


def test_agentserver_registers_exact_19_pcs_methods() -> None:
    assert server_module._PCS_REQ_METHODS == {
        item for item in ReqMethod if item.value.startswith("pcs.")
    }
    assert len(server_module._PCS_REQ_METHODS) == 19


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params", "host_method", "host_argument"),
    PCS_HOST_CALLS,
)
async def test_non_graph_methods_call_exact_host_operation(
    capture_wire: None,
    method: ReqMethod,
    params: dict[str, object],
    host_method: str,
    host_argument: object,
) -> None:
    server, host = _server()
    ws = _FakeWebSocket()

    await handle_pcs_request(host, ws, _request(method, params), asyncio.Lock())

    assert host.calls == [(host_method, host_argument)]
    assert len(ws.sent) == 1
    assert ws.sent[0]["protocol_version"] == "1.0"
    assert ws.sent[0]["is_final"] is True
    assert ws.sent[0]["status"] == "succeeded"
    assert ws.sent[0]["agent_ref"] == {"mode": "agent", "id": "agent-1"}


@pytest.mark.asyncio
async def test_select_model_rejects_old_origin_index_parameter(
    capture_wire: None,
) -> None:
    _server_instance, host = _server()
    ws = _FakeWebSocket()

    await handle_pcs_request(
        host,
        ws,
        _request(ReqMethod.PCS_RUNTIME_SELECT_MODEL, {"origin_index": 0}),
        asyncio.Lock(),
    )

    assert host.calls == []
    assert ws.sent[0]["status"] == "failed"
    assert ws.sent[0]["body"]["details"]["code"] == "BAD_REQUEST"


@pytest.mark.asyncio
async def test_non_graph_stream_request_is_one_completed_chunk(
    capture_wire: None,
) -> None:
    server, host = _server()
    ws = _FakeWebSocket()

    await handle_pcs_request(
        host,
        ws,
        _request(ReqMethod.PCS_RUNTIME_STATUS, is_stream=True),
        asyncio.Lock(),
    )

    assert host.calls == [("get_status", None)]
    assert len(ws.sent) == 1
    assert ws.sent[0]["is_stream"] is True
    assert ws.sent[0]["is_final"] is True


@pytest.mark.asyncio
async def test_graph_stream_is_bounded_ordered_and_final(
    capture_wire: None,
) -> None:
    server, host = _server()
    host.graph = {
        "context_ready": True,
        "nodes": [{"id": f"page:{index}"} for index in range(450)],
        "edges": [{"source": f"page:{index}"} for index in range(250)],
    }
    ws = _FakeWebSocket()

    await handle_pcs_request(
        host,
        ws,
        _request(ReqMethod.PCS_CONTEXT_STREAM_GRAPH, is_stream=True),
        asyncio.Lock(),
    )

    assert host.calls == [("get_graph", None)]
    assert [frame["sequence"] for frame in ws.sent] == list(range(7))
    assert [frame["is_final"] for frame in ws.sent] == [
        False,
        False,
        False,
        False,
        False,
        False,
        True,
    ]
    events = [
        frame["body"]["result"] if frame["is_final"] else frame["body"]["delta"]
        for frame in ws.sent
    ]
    assert [event["event_type"] for event in events] == [
        "pcs.graph.start",
        "pcs.graph.nodes",
        "pcs.graph.nodes",
        "pcs.graph.nodes",
        "pcs.graph.edges",
        "pcs.graph.edges",
        "pcs.graph.end",
    ]
    assert events[-1]["node_count"] == 450
    assert events[-1]["edge_count"] == 250


@pytest.mark.asyncio
async def test_graph_requires_stream_request(capture_wire: None) -> None:
    server, host = _server()
    ws = _FakeWebSocket()

    await handle_pcs_request(
        host,
        ws,
        _request(ReqMethod.PCS_CONTEXT_STREAM_GRAPH),
        asyncio.Lock(),
    )

    assert host.calls == []
    assert ws.sent[0]["status"] == "failed"
    assert ws.sent[0]["body"]["details"]["code"] == "BAD_REQUEST"


@pytest.mark.asyncio
async def test_canonical_pcs_bypasses_chat_hooks_and_agent_creation(
    capture_wire: None,
) -> None:
    server, host = _server()
    server._trigger_before_chat_request_hook = AsyncMock(
        side_effect=AssertionError("PCS must bypass chat hooks")
    )
    server._handle_unary = AsyncMock(
        side_effect=AssertionError("PCS must bypass Agent creation")
    )
    ws = _FakeWebSocket()

    await server._handle_message(
        ws,
        _canonical("pcs.runtime.status"),
        asyncio.Lock(),
    )

    assert host.calls == [("get_status", None)]
    server._trigger_before_chat_request_hook.assert_not_awaited()
    server._handle_unary.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params", "host_call"),
    [
        ("pcs.runtime.status", {}, ("get_status", None)),
        ("pcs.runtime.get_config", {}, ("get_runtime_config", None)),
        ("pcs.fetch.list_services", {}, ("list_fetch_services", None)),
        (
            "pcs.fetch.get_run_status",
            {},
            ("get_fetch_run_status", None),
        ),
        (
            "pcs.context.search_pages",
            {"query": "context"},
            ("search_graph", "context"),
        ),
        (
            "pcs.context.get_node",
            {"node_id": "page:description.md"},
            ("get_graph_page", "page:description.md"),
        ),
    ],
)
async def test_canonical_wire_round_trip_for_query_methods(
    capture_wire: None,
    method: str,
    params: dict[str, object],
    host_call: tuple[str, object],
) -> None:
    server, host = _server()
    ws = _FakeWebSocket()

    await server._handle_message(ws, _canonical(method, params), asyncio.Lock())

    response = parse_agent_server_wire_unary(ws.sent[0])
    assert host.calls == [host_call]
    assert response.ok is True


@pytest.mark.asyncio
async def test_pcs_failure_keeps_connection_and_ordinary_request_available(
    capture_wire: None,
) -> None:
    server, host = _server()
    host.failure = RuntimeError("PCS start failed")
    ws = _FakeWebSocket()

    await server._handle_message(
        ws,
        _canonical("pcs.runtime.start"),
        asyncio.Lock(),
    )

    assert ws.sent[0]["response_kind"] == "e2a.error"
    assert ws.open is True

    server._trigger_before_chat_request_hook = AsyncMock()
    server._ensure_auto_team_binding_for_chat = AsyncMock()

    async def ordinary_response(
        target_ws: _FakeWebSocket,
        request: AgentRequest,
        _send_lock: asyncio.Lock,
    ) -> None:
        wire = server_module.encode_agent_response_for_wire(
            AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"content": "ordinary agent still works"},
            ),
            response_id=request.request_id,
        )
        await server_module.send_wire_payload(target_ws, wire)

    server._handle_unary = AsyncMock(side_effect=ordinary_response)
    await server._handle_message(
        ws,
        json.dumps(
            {
                "protocol_version": "1.0",
                "request_id": "ordinary-1",
                "channel": "web",
                "method": "chat.send",
                "params": {"query": "hello"},
            }
        ),
        asyncio.Lock(),
    )

    assert ws.sent[1]["status"] == "succeeded"
    server._handle_unary.assert_awaited_once()


@pytest.mark.asyncio
async def test_core_error_is_returned_as_final_e2a_error(capture_wire: None) -> None:
    server, host = _server()
    host.failure = BaseError(StatusCode.ERROR, msg="safe PCS error")
    ws = _FakeWebSocket()

    await handle_pcs_request(
        host,
        ws,
        _request(ReqMethod.PCS_RUNTIME_STATUS),
        asyncio.Lock(),
    )

    assert ws.sent[0]["response_kind"] == "e2a.error"
    assert ws.sent[0]["body"]["details"] == {
        "error": "safe PCS error",
        "code": StatusCode.ERROR.code,
        "status": "ERROR",
    }


@pytest.mark.asyncio
async def test_handler_propagates_cancellation(capture_wire: None) -> None:
    server, host = _server()
    host.failure = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await handle_pcs_request(
            host,
            _FakeWebSocket(),
            _request(ReqMethod.PCS_RUNTIME_STATUS),
            asyncio.Lock(),
        )
