import asyncio
import json

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server import agent_ws_server as agent_ws_server_module


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


def fake_encode_agent_response_for_wire(resp, response_id):
    return {
        "response_id": response_id,
        "payload": resp.payload,
        "ok": resp.ok,
    }


def fake_encode_agent_chunk_for_wire(chunk, response_id, sequence):
    return {
        "response_id": response_id,
        "sequence": sequence,
        "payload": chunk.payload,
        "is_stream": True,
        "is_complete": chunk.is_complete,
    }


def make_large_tool_result_records(count: int = 20) -> list[dict]:
    large_result = "x" * 20_000
    return [
        {
            "id": f"tool-result-{idx}",
            "role": "teammate",
            "member_name": "agent-1",
            "event_type": "chat.tool_result",
            "mode": "team",
            "timestamp": float(idx),
            "content": large_result,
            "tool_result": {
                "tool_name": "edit_file",
                "result": large_result,
            },
        }
        for idx in range(count)
    ]


@pytest.fixture(autouse=True)
def patch_wire_encoder(monkeypatch):
    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_response_for_wire",
        fake_encode_agent_response_for_wire,
    )
    monkeypatch.setattr(
        agent_ws_server_module,
        "encode_agent_chunk_for_wire",
        fake_encode_agent_chunk_for_wire,
    )


@pytest.mark.asyncio
async def test_team_history_get_paginates_and_bounds_large_records(monkeypatch):
    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )
    ws = FakeWebSocket()
    records = make_large_tool_result_records()

    monkeypatch.setattr(
        agent_ws_server_module,
        "read_team_history_records",
        lambda session_id: records,
    )

    request = AgentRequest(
        request_id="req-team-history",
        channel_id="web",
        req_method=ReqMethod.TEAM_HISTORY_GET,
        params={"session_id": "sess-large", "limit": 20, "max_bytes": 4096},
    )

    await getattr(server, "_handle_team_history_get")(ws, request, asyncio.Lock())

    assert len(ws.sent) == 1
    frame = ws.sent[0]
    payload = frame["payload"]
    encoded_size = len(json.dumps(frame, ensure_ascii=False).encode("utf-8"))
    assert encoded_size <= 4096
    assert payload["session_id"] == "sess-large"
    assert payload["records"]
    assert len(payload["records"]) < len(records)
    assert payload["has_more"] is True
    assert payload["next_cursor"] == len(payload["records"])
    assert payload["records"][0]["content"].endswith("[truncated]")


@pytest.mark.asyncio
async def test_team_history_get_cursor_continues_next_page(monkeypatch):
    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )
    records = make_large_tool_result_records()

    monkeypatch.setattr(
        agent_ws_server_module,
        "read_team_history_records",
        lambda session_id: records,
    )

    first_ws = FakeWebSocket()
    first_request = AgentRequest(
        request_id="req-team-history-first",
        channel_id="web",
        req_method=ReqMethod.TEAM_HISTORY_GET,
        params={"session_id": "sess-large", "limit": 20, "max_bytes": 4096},
    )
    await getattr(server, "_handle_team_history_get")(first_ws, first_request, asyncio.Lock())
    first_payload = first_ws.sent[0]["payload"]

    second_ws = FakeWebSocket()
    second_request = AgentRequest(
        request_id="req-team-history-second",
        channel_id="web",
        req_method=ReqMethod.TEAM_HISTORY_GET,
        params={
            "session_id": "sess-large",
            "cursor": first_payload["next_cursor"],
            "limit": 20,
            "max_bytes": 4096,
        },
    )
    await getattr(server, "_handle_team_history_get")(second_ws, second_request, asyncio.Lock())
    second_payload = second_ws.sent[0]["payload"]

    assert first_payload["has_more"] is True
    assert second_payload["cursor"] == first_payload["next_cursor"]
    assert second_payload["records"]
    assert second_payload["records"][0]["id"] == records[first_payload["next_cursor"]]["id"]
    assert second_payload["next_cursor"] > second_payload["cursor"]
    assert len(json.dumps(second_ws.sent[0], ensure_ascii=False).encode("utf-8")) <= 4096


def test_history_get_sanitizes_large_restorable_records(monkeypatch):
    large_record = {
        "id": "tool-result-large",
        "role": "assistant",
        "event_type": "chat.tool_result",
        "content": "x" * 100_000,
        "tool_result": {
            "tool_name": "edit_file",
            "result": "x" * 100_000,
        },
    }

    monkeypatch.setattr(agent_ws_server_module, "history_exists", lambda session_id: True)
    monkeypatch.setattr(
        agent_ws_server_module,
        "load_history_records",
        lambda session_id: [large_record],
    )

    result = agent_ws_server_module.AgentWebSocketServer.get_conversation_history(
        "sess-large",
        1,
    )

    assert result is not None
    message = result["messages"][0]
    assert message["content"].endswith("[truncated]")
    assert message["tool_result"]["result"].endswith("[truncated]")
    from jiuwenswarm.server import wire_truncate as wire_truncate_module

    assert (
        len(json.dumps(message, ensure_ascii=False).encode("utf-8"))
        <= getattr(wire_truncate_module, "_HISTORY_WIRE_RECORD_MAX_BYTES")
    )


@pytest.mark.asyncio
async def test_team_history_get_preserves_too_large_first_record_as_placeholder(monkeypatch):
    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )
    ws = FakeWebSocket()
    huge_id = "tool-result-too-large-" + ("x" * 10_000)
    records = [
        {
            "id": huge_id,
            "role": "teammate",
            "member_name": "agent-1",
            "event_type": "chat.tool_result",
            "mode": "team",
            "timestamp": 1.0,
            "content": "x" * 100_000,
            "tool_result": {
                "tool_name": "edit_file",
                "result": "x" * 100_000,
            },
        }
    ]

    monkeypatch.setattr(
        agent_ws_server_module,
        "read_team_history_records",
        lambda session_id: records,
    )

    request = AgentRequest(
        request_id="req-team-history-placeholder",
        channel_id="web",
        req_method=ReqMethod.TEAM_HISTORY_GET,
        params={"session_id": "sess-large", "limit": 20, "max_bytes": 2048},
    )

    await getattr(server, "_handle_team_history_get")(ws, request, asyncio.Lock())

    payload = ws.sent[0]["payload"]
    encoded_size = len(json.dumps(ws.sent[0], ensure_ascii=False).encode("utf-8"))
    assert encoded_size <= 2048
    assert len(payload["records"]) == 1
    assert payload["next_cursor"] == 1
    assert payload["has_more"] is False
    assert payload["records"][0]["truncated"] is True
    assert payload["records"][0]["id"].startswith("tool-result-too-large-")
    assert payload["records"][0]["event_type"] == "chat.tool_result"


# ---------------------------------------------------------------------------
# Diagram carve-out — issue #2568
# ---------------------------------------------------------------------------

def make_svg_message(rect_count: int = 500) -> str:
    """A fenced ```svg block comfortably larger than the 16 KB string limit."""
    rects = "".join(
        f'<rect x="{idx}" y="{idx}" width="10" height="10" fill="#1a2a6c"/>'
        for idx in range(rect_count)
    )
    svg = f'<svg width="800" height="600" xmlns="http://www.w3.org/2000/svg">{rects}</svg>'
    return "```svg\n" + svg + "\n```"


def make_svg_record(record_id: str = "svg-1:assistant", rect_count: int = 500) -> dict:
    return {
        "id": record_id,
        "role": "assistant",
        "request_id": record_id.split(":")[0],
        "channel_id": "web",
        "event_type": "chat.final",
        "mode": "chat",
        "timestamp": 1.0,
        "content": make_svg_message(rect_count),
    }


def test_history_get_preserves_large_svg_content(monkeypatch):
    """A large fenced SVG must survive history replay byte-for-byte (#2568)."""
    from jiuwenswarm.server import wire_truncate as wire_truncate_module

    record = make_svg_record()
    content = record["content"]
    # Guard the fixture: without the carve-out this would be truncated.
    assert len(content.encode("utf-8")) > wire_truncate_module._HISTORY_WIRE_STRING_LIMIT

    monkeypatch.setattr(agent_ws_server_module, "history_exists", lambda session_id: True)
    monkeypatch.setattr(
        agent_ws_server_module,
        "load_history_records",
        lambda session_id: [record],
    )

    result = agent_ws_server_module.AgentWebSocketServer.get_conversation_history(
        "sess-svg",
        1,
    )

    assert result is not None
    message = result["messages"][0]
    assert message["content"] == content
    assert "[truncated]" not in message["content"]
    assert message.get("truncated") is None
    # Still parses as a closed fence, so the renderer sees a complete block.
    assert message["content"].rstrip().endswith("```")


def test_svg_beyond_diagram_ceiling_is_still_truncated():
    """The carve-out raises the ceiling; it does not remove it."""
    from jiuwenswarm.server import wire_truncate as wire_truncate_module

    ceiling = wire_truncate_module._HISTORY_WIRE_DIAGRAM_LIMIT
    padding = "<!-- " + ("p" * (ceiling + 1024)) + " -->"
    oversized = "```svg\n<svg xmlns=\"http://www.w3.org/2000/svg\">" + padding + "</svg>\n```"
    assert len(oversized.encode("utf-8")) > ceiling

    sanitized = wire_truncate_module._sanitize_history_wire_value(oversized)

    assert sanitized.endswith("[truncated]")
    assert len(sanitized.encode("utf-8")) <= ceiling


def test_plain_content_still_truncates_at_base_limit():
    """Non-diagram strings keep the original 16 KB budget."""
    from jiuwenswarm.server import wire_truncate as wire_truncate_module

    sanitized = wire_truncate_module._sanitize_history_wire_value("x" * 100_000)

    assert sanitized.endswith("[truncated]")
    assert (
        len(sanitized.encode("utf-8"))
        <= wire_truncate_module._HISTORY_WIRE_STRING_LIMIT
    )


def test_diagram_detection_matches_renderer_contract():
    """Detection keys off the fence language, like the web renderer does."""
    from jiuwenswarm.server import wire_truncate as wire_truncate_module

    detect = wire_truncate_module._contains_diagram_markup

    assert detect("intro\n```svg\n<svg></svg>\n```")
    assert detect("```mermaid\ngraph TD;\n```")
    assert detect("```SVG\n<svg></svg>\n```")
    # Bare markup is not rendered as a diagram, so it gets no carve-out.
    assert not detect('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    assert not detect("```svgx\nnot a diagram\n```")
    assert not detect("plain prose about ```svg blocks is fine")


def test_svg_record_is_not_collapsed_to_a_stub():
    """A diagram record above the 64 KB record budget keeps its markup."""
    from jiuwenswarm.server import wire_truncate as wire_truncate_module

    record = make_svg_record(rect_count=2000)
    assert (
        wire_truncate_module._json_wire_size(record)
        > wire_truncate_module._HISTORY_WIRE_RECORD_MAX_BYTES
    )

    sanitized = wire_truncate_module._sanitize_history_record_for_wire(record)

    assert sanitized["content"] == record["content"]
    assert sanitized.get("truncated") is None


@pytest.mark.asyncio
async def test_team_history_get_gives_large_svg_its_own_page(monkeypatch):
    """An SVG over the page budget gets a page to itself instead of a stub."""
    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )
    ws = FakeWebSocket()
    svg_record = make_svg_record(record_id="svg-big:assistant", rect_count=3000)
    records = [svg_record, make_svg_record(record_id="svg-next:assistant")]

    monkeypatch.setattr(
        agent_ws_server_module,
        "read_team_history_records",
        lambda session_id: records,
    )

    request = AgentRequest(
        request_id="req-team-history-svg",
        channel_id="web",
        req_method=ReqMethod.TEAM_HISTORY_GET,
        params={"session_id": "sess-svg", "limit": 20, "max_bytes": 8192},
    )

    await getattr(server, "_handle_team_history_get")(ws, request, asyncio.Lock())

    payload = ws.sent[0]["payload"]
    assert len(payload["records"]) == 1
    assert payload["records"][0]["content"] == svg_record["content"]
    assert payload["records"][0].get("truncated") is None
    assert payload["next_cursor"] == 1
    assert payload["has_more"] is True


def test_non_stream_history_page_is_bounded():
    """The non-streaming page fitter shrinks a page that cannot fit one frame."""
    from jiuwenswarm.server import wire_truncate as wire_truncate_module

    budget = 256 * 1024
    messages = [
        make_svg_record(record_id=f"svg-{idx}:assistant", rect_count=3000)
        for idx in range(8)
    ]
    assert wire_truncate_module._json_wire_size(messages) > budget

    fitted = wire_truncate_module._fit_history_page_to_budget(
        messages,
        max_bytes=budget,
    )

    # Message count is preserved — the non-streaming contract has no cursor.
    assert len(fitted) == len(messages)
    assert wire_truncate_module._json_wire_size(fitted) <= budget


async def _call_record_get(server, params: dict) -> dict:
    ws = FakeWebSocket()
    request = AgentRequest(
        request_id="req-record-get",
        channel_id="web",
        req_method=ReqMethod.HISTORY_RECORD_GET,
        params=params,
    )
    await getattr(server, "_handle_history_record_get")(ws, request, asyncio.Lock())
    return ws.sent[0]


@pytest.mark.asyncio
async def test_history_record_get_returns_untruncated_content(monkeypatch):
    """Export path: fetch one record's full content straight from disk (#2568)."""
    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )
    # Sized into the window where history.get truncates but a single-record
    # fetch does not: above the diagram ceiling, below the send budget.
    record = make_svg_record(record_id="svg-full:assistant", rect_count=60_000)
    from jiuwenswarm.server import wire_truncate as wire_truncate_module

    content_bytes = len(record["content"].encode("utf-8"))
    assert content_bytes > wire_truncate_module._HISTORY_WIRE_DIAGRAM_LIMIT
    assert content_bytes < agent_ws_server_module._HISTORY_RECORD_GET_MAX_BYTES
    # Same record through the page path is truncated ...
    paged = wire_truncate_module._sanitize_history_record_for_wire(record)
    assert paged["content"].endswith("[truncated]")

    monkeypatch.setattr(agent_ws_server_module, "history_exists", lambda session_id: True)
    monkeypatch.setattr(
        agent_ws_server_module,
        "load_history_records",
        lambda session_id: [record],
    )

    frame = await _call_record_get(
        server,
        {"session_id": "sess-svg", "record_id": "svg-full:assistant"},
    )

    # ... but the record fetch returns it whole.
    assert frame["ok"] is True
    assert frame["payload"]["content"] == record["content"]
    assert frame["payload"]["truncated"] is False
    assert "[truncated]" not in frame["payload"]["content"]


@pytest.mark.asyncio
async def test_history_record_get_rejects_bad_requests(monkeypatch):
    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )
    monkeypatch.setattr(agent_ws_server_module, "history_exists", lambda session_id: True)
    monkeypatch.setattr(
        agent_ws_server_module,
        "load_history_records",
        lambda session_id: [make_svg_record(record_id="svg-1:assistant")],
    )

    missing_session = await _call_record_get(server, {"record_id": "svg-1:assistant"})
    assert missing_session["ok"] is False

    missing_record = await _call_record_get(server, {"session_id": "s"})
    assert missing_record["ok"] is False

    # Only whitelisted fields are readable — not an arbitrary record reader.
    bad_field = await _call_record_get(
        server,
        {"session_id": "s", "record_id": "svg-1:assistant", "field": "tool_result"},
    )
    assert bad_field["ok"] is False

    unknown = await _call_record_get(
        server,
        {"session_id": "s", "record_id": "does-not-exist"},
    )
    assert unknown["ok"] is False
    assert unknown["payload"]["error"] == "record not found"


@pytest.mark.asyncio
async def test_history_record_get_still_bounded_by_send_budget(monkeypatch):
    """The escape hatch is unbounded relative to history.get, not absolutely."""
    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )
    ceiling = agent_ws_server_module._HISTORY_RECORD_GET_MAX_BYTES
    record = {
        "id": "huge:assistant",
        "role": "assistant",
        "event_type": "chat.final",
        "content": "```svg\n<svg>" + ("z" * (ceiling + 4096)) + "</svg>\n```",
    }

    monkeypatch.setattr(agent_ws_server_module, "history_exists", lambda session_id: True)
    monkeypatch.setattr(
        agent_ws_server_module,
        "load_history_records",
        lambda session_id: [record],
    )

    frame = await _call_record_get(
        server,
        {"session_id": "s", "record_id": "huge:assistant"},
    )

    assert frame["ok"] is True
    assert frame["payload"]["truncated"] is True
    assert len(frame["payload"]["content"].encode("utf-8")) <= ceiling


def test_non_stream_history_page_left_alone_when_it_fits():
    """Pages within budget pass through untouched."""
    from jiuwenswarm.server import wire_truncate as wire_truncate_module

    messages = [make_svg_record(record_id=f"svg-{idx}:assistant") for idx in range(2)]

    fitted = wire_truncate_module._fit_history_page_to_budget(
        messages,
        max_bytes=wire_truncate_module._HISTORY_FRAME_WIRE_MAX_BYTES,
    )

    assert fitted == messages


# ---------------------------------------------------------------------------
# Streaming / non-streaming parity for the diagram carve-out
# ---------------------------------------------------------------------------

async def _stream_history_messages(server, session_id: str = "sess-svg") -> list[dict]:
    """Drive ``_handle_history_get_stream`` and collect its per-record chunks."""
    ws = FakeWebSocket()
    request = AgentRequest(
        request_id="req-history-stream",
        channel_id="web",
        req_method=ReqMethod.HISTORY_GET,
        params={"session_id": session_id, "page_idx": 1},
        is_stream=True,
    )
    await getattr(server, "_handle_history_get_stream")(ws, request, asyncio.Lock())
    return [
        frame["payload"]["message"]
        for frame in ws.sent
        if "message" in frame.get("payload", {})
    ]


async def _get_history_messages(server, session_id: str = "sess-svg") -> list[dict]:
    """Drive the non-streaming ``_handle_history_get`` and return its page."""
    ws = FakeWebSocket()
    request = AgentRequest(
        request_id="req-history-get",
        channel_id="web",
        req_method=ReqMethod.HISTORY_GET,
        params={"session_id": session_id, "page_idx": 1},
    )
    await getattr(server, "_handle_history_get")(ws, request, asyncio.Lock())
    assert ws.sent[0]["ok"] is True
    return ws.sent[0]["payload"]["messages"]


def _patch_history_source(monkeypatch, records: list[dict]) -> None:
    monkeypatch.setattr(agent_ws_server_module, "history_exists", lambda session_id: True)
    monkeypatch.setattr(
        agent_ws_server_module,
        "load_history_records",
        lambda session_id: records,
    )


def test_multi_diagram_page_is_budgeted_against_the_frame_not_one_diagram():
    """A page of several SVGs fits one frame, so none of them may be dropped.

    Regression: the page budget used to be ``_HISTORY_WIRE_DIAGRAM_LIMIT`` — a
    per-string ceiling — which made any two large diagrams on one page collapse
    the biggest one to a 512-byte stub.
    """
    from jiuwenswarm.server import wire_truncate as wire_truncate_module

    messages = [
        wire_truncate_module._sanitize_history_record_for_wire(
            make_svg_record(record_id=f"svg-{idx}:assistant", rect_count=20_000)
        )
        for idx in range(2)
    ]
    page_bytes = wire_truncate_module._json_wire_size(messages)
    # The window this test is about: over the diagram ceiling, under one frame.
    assert page_bytes > wire_truncate_module._HISTORY_WIRE_DIAGRAM_LIMIT
    assert page_bytes <= wire_truncate_module._HISTORY_FRAME_WIRE_MAX_BYTES

    fitted = wire_truncate_module._fit_history_page_to_budget(
        messages,
        max_bytes=wire_truncate_module._HISTORY_FRAME_WIRE_MAX_BYTES,
    )

    assert fitted == messages
    for message in fitted:
        assert "[truncated]" not in message["content"]


def test_page_fitter_sacrifices_ordinary_records_before_diagrams():
    """When a page truly cannot fit, the picture outlives the chatter."""
    from jiuwenswarm.server import wire_truncate as wire_truncate_module

    diagram = wire_truncate_module._sanitize_history_record_for_wire(
        make_svg_record(record_id="svg-keep:assistant", rect_count=3000)
    )
    ordinary = [
        wire_truncate_module._sanitize_history_record_for_wire(
            {
                "id": f"tool-{idx}",
                "role": "assistant",
                "event_type": "chat.tool_result",
                "content": "x" * 20_000,
            }
        )
        for idx in range(20)
    ]
    messages = [diagram, *ordinary]
    # Budget leaves room for the diagram but not for the diagram plus the bulk.
    budget = wire_truncate_module._json_wire_size(diagram) + 8 * 1024
    assert wire_truncate_module._json_wire_size(messages) > budget

    fitted = wire_truncate_module._fit_history_page_to_budget(
        messages,
        max_bytes=budget,
    )

    assert len(fitted) == len(messages)
    assert wire_truncate_module._json_wire_size(fitted) <= budget
    assert fitted[0]["content"] == diagram["content"]
    assert all(record.get("truncated") is True for record in fitted[1:])


@pytest.mark.asyncio
async def test_history_get_stream_and_non_stream_agree(monkeypatch):
    """Both ``history.get`` handlers must shape the same history identically."""
    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )
    records = [
        make_svg_record(record_id=f"svg-{idx}:assistant", rect_count=20_000)
        for idx in range(2)
    ]
    _patch_history_source(monkeypatch, records)

    paged = await _get_history_messages(server)
    streamed = await _stream_history_messages(server)

    assert [record["id"] for record in paged] == [record["id"] for record in streamed]
    assert [record["content"] for record in paged] == [
        record["content"] for record in streamed
    ]
    # And both keep the diagrams whole.
    for record in paged:
        assert "[truncated]" not in record["content"]


@pytest.mark.asyncio
async def test_history_get_stream_degrades_oversized_record_instead_of_aborting(
    monkeypatch,
):
    """One over-budget record must not truncate the rest of the streamed page."""
    from jiuwenswarm.server import wire_truncate as wire_truncate_module

    server = agent_ws_server_module.AgentWebSocketServer.__new__(
        agent_ws_server_module.AgentWebSocketServer
    )
    records = [
        make_svg_record(record_id="svg-huge:assistant", rect_count=3000),
        make_svg_record(record_id="svg-small:assistant", rect_count=10),
    ]
    _patch_history_source(monkeypatch, records)
    # Squeeze the frame budget so the first record cannot fit as-is.
    tiny_budget = wire_truncate_module._json_wire_size(
        wire_truncate_module._sanitize_history_record_for_wire(records[0])
    ) // 2
    monkeypatch.setattr(
        agent_ws_server_module,
        "_HISTORY_FRAME_WIRE_MAX_BYTES",
        tiny_budget,
    )

    ws = FakeWebSocket()
    request = AgentRequest(
        request_id="req-history-stream-oversized",
        channel_id="web",
        req_method=ReqMethod.HISTORY_GET,
        params={"session_id": "sess-svg", "page_idx": 1},
        is_stream=True,
    )
    await getattr(server, "_handle_history_get_stream")(ws, request, asyncio.Lock())

    messages = [
        frame["payload"]["message"]
        for frame in ws.sent
        if "message" in frame.get("payload", {})
    ]
    # Newest-first ordering: the small record streams first, the huge one is
    # degraded rather than dropped, and the terminal "done" frame still arrives.
    assert len(messages) == 2
    degraded = next(m for m in messages if m["id"] == "svg-huge:assistant")
    assert degraded["truncated"] is True
    assert wire_truncate_module._json_wire_size(degraded) <= tiny_budget
    assert ws.sent[-1]["payload"]["status"] == "done"
    assert ws.sent[-1]["is_complete"] is True
