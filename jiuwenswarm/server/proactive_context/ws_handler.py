# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PCS WebSocket request dispatch on JiuwenSwarm's existing E2A wire path."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, cast

from openjiuwen.core.common.exception.errors import BaseError

from jiuwenswarm.common.e2a.wire_codec import (
    encode_agent_chunk_for_wire,
    encode_agent_response_for_wire,
)
from jiuwenswarm.common.schema.agent import (
    AgentRequest,
    AgentResponse,
    AgentResponseChunk,
)
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.ws_send import send_wire_payload

if TYPE_CHECKING:
    from jiuwenswarm.server.proactive_context.host_api import PCSHostAPI


logger = logging.getLogger(__name__)

PCS_REQUEST_METHODS = frozenset(
    {
        ReqMethod.PCS_RUNTIME_STATUS,
        ReqMethod.PCS_RUNTIME_START,
        ReqMethod.PCS_RUNTIME_STOP,
        ReqMethod.PCS_RUNTIME_GET_CONFIG,
        ReqMethod.PCS_RUNTIME_PATCH_CONFIG,
        ReqMethod.PCS_RUNTIME_SELECT_MODEL,
        ReqMethod.PCS_FETCH_LIST_SERVICES,
        ReqMethod.PCS_FETCH_PATCH_SERVICE,
        ReqMethod.PCS_FETCH_START_SERVICE,
        ReqMethod.PCS_FETCH_STOP_SERVICE,
        ReqMethod.PCS_FETCH_START_SCHEDULER,
        ReqMethod.PCS_FETCH_STOP_SCHEDULER,
        ReqMethod.PCS_FETCH_RUN_ALL,
        ReqMethod.PCS_FETCH_RUN_ONE,
        ReqMethod.PCS_FETCH_GET_RUN_STATUS,
        ReqMethod.PCS_FETCH_AUTHORIZE_PROVIDER,
        ReqMethod.PCS_CONTEXT_STREAM_GRAPH,
        ReqMethod.PCS_CONTEXT_SEARCH_PAGES,
        ReqMethod.PCS_CONTEXT_GET_NODE,
    }
)


def _payload(value: object) -> dict[str, object]:
    if value is None:
        return {"ok": True}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    raise TypeError("PCS Host result must be an object")


def _text(params: dict[str, object], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


async def _send_result(
    ws: Any,
    request: AgentRequest,
    send_lock: asyncio.Lock,
    payload: dict[str, object],
) -> None:
    if request.is_stream:
        wire = encode_agent_chunk_for_wire(
            AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload=payload,
                is_complete=True,
                agent_ref=request.agent_ref,
            ),
            response_id=request.request_id,
            sequence=0,
        )
    else:
        wire = encode_agent_response_for_wire(
            AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
                agent_ref=request.agent_ref,
            ),
            response_id=request.request_id,
        )
    async with send_lock:
        await send_wire_payload(ws, wire)


async def _send_error(
    ws: Any,
    request: AgentRequest,
    send_lock: asyncio.Lock,
    *,
    message: str,
    code: object,
    status: str | None = None,
) -> None:
    payload: dict[str, object] = {"error": message, "code": code}
    if status is not None:
        payload["status"] = status
    if request.is_stream:
        payload["event_type"] = "chat.error"
        wire = encode_agent_chunk_for_wire(
            AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload=payload,
                is_complete=True,
                agent_ref=request.agent_ref,
            ),
            response_id=request.request_id,
            sequence=0,
        )
    else:
        wire = encode_agent_response_for_wire(
            AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload=payload,
                agent_ref=request.agent_ref,
            ),
            response_id=request.request_id,
        )
    async with send_lock:
        await send_wire_payload(ws, wire)


async def _stream_graph(
    host: PCSHostAPI,
    ws: Any,
    request: AgentRequest,
    send_lock: asyncio.Lock,
) -> None:
    graph = await host.get_graph()
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise TypeError("PCS graph nodes and edges must be arrays")
    events: list[dict[str, object]] = [
        {
            "event_type": "pcs.graph.start",
            "context_ready": bool(graph.get("context_ready")),
        }
    ]
    events.extend(
        {"event_type": "pcs.graph.nodes", "nodes": nodes[index : index + 200]}
        for index in range(0, len(nodes), 200)
    )
    events.extend(
        {"event_type": "pcs.graph.edges", "edges": edges[index : index + 200]}
        for index in range(0, len(edges), 200)
    )
    events.append(
        {
            "event_type": "pcs.graph.end",
            "node_count": len(nodes),
            "edge_count": len(edges),
        }
    )
    for sequence, event in enumerate(events):
        wire = encode_agent_chunk_for_wire(
            AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload=event,
                is_complete=sequence == len(events) - 1,
                agent_ref=request.agent_ref,
            ),
            response_id=request.request_id,
            sequence=sequence,
        )
        async with send_lock:
            await send_wire_payload(ws, wire)


async def _execute(host: PCSHostAPI, request: AgentRequest) -> dict[str, object]:
    method = request.req_method
    params = request.params
    if method == ReqMethod.PCS_RUNTIME_STATUS:
        return _payload(await host.get_status())
    if method == ReqMethod.PCS_RUNTIME_START:
        return _payload(await host.set_runtime_enabled(True))
    if method == ReqMethod.PCS_RUNTIME_STOP:
        return _payload(await host.set_runtime_enabled(False))
    if method == ReqMethod.PCS_RUNTIME_GET_CONFIG:
        return await host.get_runtime_config()
    if method == ReqMethod.PCS_RUNTIME_PATCH_CONFIG:
        return await host.patch_runtime_config(
            cast(dict[str, object], params.get("patch"))
        )
    if method == ReqMethod.PCS_RUNTIME_SELECT_MODEL:
        return await host.select_model(cast(int, params.get("origin_index")))
    if method == ReqMethod.PCS_FETCH_LIST_SERVICES:
        return {"services": await host.list_fetch_services()}
    if method == ReqMethod.PCS_FETCH_PATCH_SERVICE:
        return await host.patch_fetch_service(
            _text(params, "service_id"),
            cast(dict[str, object], params.get("patch")),
        )
    if method in {ReqMethod.PCS_FETCH_START_SERVICE, ReqMethod.PCS_FETCH_STOP_SERVICE}:
        await host.set_fetching(
            enabled=method == ReqMethod.PCS_FETCH_START_SERVICE,
            service_id=_text(params, "service_id"),
        )
        return {"ok": True}
    if method in {
        ReqMethod.PCS_FETCH_START_SCHEDULER,
        ReqMethod.PCS_FETCH_STOP_SCHEDULER,
    }:
        await host.set_fetching(
            enabled=method == ReqMethod.PCS_FETCH_START_SCHEDULER,
        )
        return {"ok": True}
    if method == ReqMethod.PCS_FETCH_RUN_ALL:
        return await host.run_fetch()
    if method == ReqMethod.PCS_FETCH_RUN_ONE:
        return await host.run_fetch(service_id=_text(params, "service_id"))
    if method == ReqMethod.PCS_FETCH_GET_RUN_STATUS:
        return await host.get_fetch_run_status(
            cast(str | None, params.get("service_id"))
        )
    if method == ReqMethod.PCS_FETCH_AUTHORIZE_PROVIDER:
        return await host.authorize_provider(_text(params, "provider"))
    if method == ReqMethod.PCS_CONTEXT_SEARCH_PAGES:
        return await host.search_graph(_text(params, "query"))
    if method == ReqMethod.PCS_CONTEXT_GET_NODE:
        return await host.get_graph_page(_text(params, "node_id"))
    raise ValueError("unknown PCS method")


async def handle_pcs_request(
    host: PCSHostAPI,
    ws: Any,
    request: AgentRequest,
    send_lock: asyncio.Lock,
) -> None:
    """Execute one parsed PCS request without introducing a PCS wire protocol."""

    try:
        if request.req_method == ReqMethod.PCS_CONTEXT_STREAM_GRAPH:
            if not request.is_stream:
                raise ValueError("pcs.context.stream_graph requires is_stream=true")
            await _stream_graph(host, ws, request, send_lock)
            return
        await _send_result(ws, request, send_lock, await _execute(host, request))
    except asyncio.CancelledError:
        raise
    except ValueError as exc:
        await _send_error(
            ws,
            request,
            send_lock,
            message=str(exc),
            code="BAD_REQUEST",
        )
    except BaseError as exc:
        await _send_error(
            ws,
            request,
            send_lock,
            message=exc.message,
            code=exc.code,
            status=exc.status.name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("PCS request failed: %s", type(exc).__name__)
        await _send_error(
            ws,
            request,
            send_lock,
            message="PCS request failed",
            code="INTERNAL_ERROR",
        )
