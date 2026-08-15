# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for handle_steer — the chat.steer adapter path.

chat.steer is a short, non-streaming chat method: it queues text for a round
that is already running and reports what happened. The two properties that
matter here are that it never claims an output lease, and that the reply
carries the dispatch disposition rather than a bare success flag.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


class _FakeResult:
    """Stands in for agent-core's SendInputResult."""

    def __init__(self, accepted: bool, disposition: str, reason: str | None = None) -> None:
        self.accepted = accepted
        self.disposition = MagicMock(value=disposition)
        self.reason = reason


def _make_handler(instance: object | None = None) -> JiuWenSwarmDeepAdapter:
    """Bypass the heavy __init__; the handler only needs these two attributes."""
    adapter = JiuWenSwarmDeepAdapter.__new__(JiuWenSwarmDeepAdapter)
    adapter._is_session_scoped_adapter = True
    adapter._instance = instance
    return adapter


def _fake_instance(result: _FakeResult | Exception) -> MagicMock:
    instance = MagicMock()
    if isinstance(result, Exception):
        instance.send_input = AsyncMock(side_effect=result)
    else:
        instance.send_input = AsyncMock(return_value=result)
    instance.attach_output = AsyncMock()
    return instance


def _req(
    query: object = "prefer the async client",
    *,
    expected_round_id: str | None = None,
) -> AgentRequest:
    params: dict[str, object] = {"query": query, "mode": "agent"}
    if expected_round_id is not None:
        params["expected_round_id"] = expected_round_id
    return AgentRequest(
        request_id="req-steer-1",
        channel_id="web",
        session_id="sess-1",
        req_method=ReqMethod.CHAT_STEER,
        params=params,
    )


@pytest.mark.anyio
async def test_steer_never_attaches_output() -> None:
    """The steered turn already has a consumer; claiming a lease would replace it."""
    instance = _fake_instance(_FakeResult(True, "steer_queued"))
    handler = _make_handler(instance)

    await handler.handle_steer(_req())

    instance.attach_output.assert_not_called()
    instance.send_input.assert_awaited_once()


@pytest.mark.anyio
async def test_accepted_steer_reports_its_disposition() -> None:
    instance = _fake_instance(_FakeResult(True, "steer_queued"))
    handler = _make_handler(instance)

    resp = await handler.handle_steer(_req())

    assert resp.ok is True
    assert resp.payload["request_id"] == "req-steer-1"
    assert resp.payload["accepted"] is True
    assert resp.payload["disposition"] == "steer_queued"
    assert resp.payload["target"] == "agent"
    assert "reason" not in resp.payload
    # No event_type: the gateway would turn this reply into an event frame and
    # the client's awaited RPC would never resolve. See the wire-level test in
    # tests/unit_tests/gateway/test_message_handler_stream_cancel.py.
    assert "event_type" not in resp.payload


@pytest.mark.anyio
async def test_steer_forwards_expected_round_id() -> None:
    instance = _fake_instance(_FakeResult(True, "steer_queued"))
    handler = _make_handler(instance)

    await handler.handle_steer(_req(expected_round_id="round-a"))

    instance.send_input.assert_awaited_once()
    req = instance.send_input.await_args.args[0]
    assert req.expected_round_id == "round-a"


@pytest.mark.anyio
async def test_round_mismatch_reason_surfaces_on_ack() -> None:
    instance = _fake_instance(_FakeResult(False, "rejected", "round_mismatch"))
    handler = _make_handler(instance)

    resp = await handler.handle_steer(_req(expected_round_id="round-b"))

    assert resp.ok is True
    assert resp.payload["accepted"] is False
    assert resp.payload["reason"] == "round_mismatch"


@pytest.mark.anyio
async def test_goal_supplement_is_reported_as_follow_up_not_as_steer() -> None:
    """The disposition is why the ACK carries more than a boolean.

    While a Goal is ACTIVE the Web sends ordinary input over the steer path.
    Between attempts it lands as a follow-up, which affects the Goal's *next*
    attempt rather than the answer streaming now. A client that only saw
    ``accepted: true`` could not tell those apart and would render the wrong
    thing.
    """
    instance = _fake_instance(_FakeResult(True, "follow_up_queued"))
    handler = _make_handler(instance)

    resp = await handler.handle_steer(_req())

    assert resp.payload["accepted"] is True
    assert resp.payload["disposition"] == "follow_up_queued"


@pytest.mark.anyio
async def test_stale_steer_is_rejected_without_failing_the_request() -> None:
    """A rejection is an answer, not a server fault: ok stays true."""
    instance = _fake_instance(_FakeResult(False, "rejected", "no_active_round"))
    handler = _make_handler(instance)

    resp = await handler.handle_steer(_req())

    assert resp.ok is True
    assert resp.payload["accepted"] is False
    assert resp.payload["reason"] == "no_active_round"
    assert resp.payload["disposition"] == "rejected"


@pytest.mark.anyio
async def test_terminated_interaction_is_a_rejection_not_a_crash() -> None:
    """send_input raises when the loop never started or already ended."""
    instance = _fake_instance(RuntimeError("interaction_terminated"))
    handler = _make_handler(instance)

    resp = await handler.handle_steer(_req())

    assert resp.ok is True
    assert resp.payload["accepted"] is False
    assert resp.payload["reason"] == "interaction_terminated"


@pytest.mark.anyio
async def test_other_runtime_errors_are_not_disguised_as_rejections() -> None:
    """A broken session must not report a healthy "nothing to steer".

    send_input also raises RuntimeError for genuine faults -- an active round
    with no loop_controller, for one. Swallowing those into an ACK would tell
    the user their steer simply arrived too late while the session is actually
    broken, and would hide the fault from anyone reading logs.
    """
    instance = _fake_instance(
        RuntimeError("active interaction round cannot accept steer without loop_controller")
    )
    handler = _make_handler(instance)

    with pytest.raises(RuntimeError, match="loop_controller"):
        await handler.handle_steer(_req())


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field, value",
    [
        ("attachments", [{"name": "spec.pdf"}]),
        ("files", {"a.py": "x"}),
        ("media_items", [{"url": "http://x/y.png"}]),
    ],
)
async def test_attachments_are_rejected_rather_than_silently_dropped(
    field: str, value: object
) -> None:
    """Steering is text-only; the queue carries strings and nothing else.

    Accepting the request and discarding the file would look like success to a
    user who believes they attached something.
    """
    instance = _fake_instance(_FakeResult(True, "steer_queued"))
    handler = _make_handler(instance)
    request = _req()
    request.params[field] = value

    resp = await handler.handle_steer(request)

    assert resp.payload["accepted"] is False
    assert resp.payload["reason"] == "attachments_not_supported"
    instance.send_input.assert_not_awaited()


@pytest.mark.anyio
async def test_empty_attachment_fields_do_not_block_a_steer() -> None:
    """An empty list is not an attachment; only a populated field rejects."""
    instance = _fake_instance(_FakeResult(True, "steer_queued"))
    handler = _make_handler(instance)
    request = _req()
    request.params.update({"attachments": [], "files": {}, "media_items": None})

    resp = await handler.handle_steer(request)

    assert resp.payload["accepted"] is True


@pytest.mark.anyio
@pytest.mark.parametrize("bad_query", ["", "   ", None, 123])
async def test_empty_or_non_text_steer_is_rejected_before_dispatch(bad_query: object) -> None:
    """A steer is text-only, and an empty one must not reach the agent."""
    instance = _fake_instance(_FakeResult(True, "steer_queued"))
    handler = _make_handler(instance)

    resp = await handler.handle_steer(_req(bad_query))

    assert resp.payload["accepted"] is False
    assert resp.payload["reason"] == "empty_query"
    instance.send_input.assert_not_awaited()


@pytest.mark.anyio
async def test_steer_without_an_agent_instance_is_rejected() -> None:
    handler = _make_handler(None)

    resp = await handler.handle_steer(_req())

    assert resp.ok is True
    assert resp.payload["accepted"] is False
    assert resp.payload["reason"] == "no_agent_instance"


@pytest.mark.anyio
async def test_send_input_returning_none_is_a_clear_rejection() -> None:
    """Pinned cores that still return None must not AttributeError after dispatch.

    Against an old agent-core, send_input enqueues the text and returns None.
    Dereferencing result.accepted then crashes *after* the model already has the
    instruction, so the client reports failure and the user steers twice.
    """
    instance = _fake_instance(None)  # type: ignore[arg-type]
    handler = _make_handler(instance)

    resp = await handler.handle_steer(_req())

    assert resp.ok is True
    assert resp.payload["accepted"] is False
    assert resp.payload["reason"] == "unsupported_runtime"
    instance.send_input.assert_awaited_once()


@pytest.mark.anyio
async def test_steer_forwards_skills_in_send_input_inputs() -> None:
    instance = _fake_instance(_FakeResult(True, "steer_queued"))
    handler = _make_handler(instance)
    request = _req("use the reviewer")
    request.params["skills"] = ["code-review"]

    resp = await handler.handle_steer(request)

    assert resp.payload["accepted"] is True
    kwargs = instance.send_input.await_args.args[0]
    assert kwargs.inputs["query"] == "use the reviewer"
    assert kwargs.inputs["skills"] == ["code-review"]


# ------------------------------------------------- the Goal supplement guard


def test_legacy_steer_wire_form_still_routes_through_the_attach_path() -> None:
    """Guard for the Goal supplement. Read this before changing steer routing.

    While a Goal is ACTIVE the Web sends *every* ordinary input as
    ``chat.send`` with ``input_mode="steer"``
    (``channels/web/frontend/src/hooks/useWebSocket.ts:1474``), so the text
    lands as a supplementary constraint on the running Goal instead of
    overwriting it. That input is not steering; it only shares the wire field.

    It must keep taking the streaming branch, where
    ``_should_inject_into_existing_interaction`` is true and the adapter calls
    ``attach_output()`` before ``send_input()``. That ``attach_output`` is how
    the supplement becomes the stream reader when the session is idle. Route it
    to ``handle_steer`` instead -- which never attaches, by design -- and an
    ACTIVE Goal's output is left with no consumer.

    The tempting change this guards against is removing ``steer`` from
    ``_is_ack_only_dispatch`` on the grounds that steering now has its own
    handler. It does, but only for the ``chat.steer`` method; the legacy form
    keeps the old path until section 6 migrates the Web.
    """
    handler = _make_handler()
    goal_supplement = {"mode": "agent", "query": "also check the retry budget",
                       "input_mode": "steer"}

    from openjiuwen.harness.schema.interaction import InputDispatchMode

    assert handler._resolve_input_dispatch_mode(goal_supplement) is InputDispatchMode.STEER
    assert handler._should_inject_into_existing_interaction(goal_supplement) is True
    assert handler._is_ack_only_dispatch(goal_supplement) is True

    # runtime_mode is the same wire form under its older name.
    runtime_mode_variant = {"mode": "agent", "query": "x", "runtime_mode": "steer"}
    assert handler._should_inject_into_existing_interaction(runtime_mode_variant) is True

    # Contrast: ordinary input carries neither field and does not take that
    # branch, so the assertion above cannot be satisfied by making the
    # predicate true for everything.
    assert handler._should_inject_into_existing_interaction({"mode": "agent"}) is False


def test_the_two_wire_forms_do_not_share_a_handler() -> None:
    """chat.steer is dispatched by method identity, never by a predicate.

    The two forms converge on the same steering *service* -- both end in
    send_input with mode=STEER -- but not on the same lease behaviour, because
    only the legacy one carries Goal supplements that need the reader. Widening
    this dispatch to a predicate over both forms is the change that breaks the
    Goal, and it is the remedy an earlier review proposed.
    """
    import inspect

    from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm

    source = inspect.getsource(JiuWenSwarm.process_message)
    assert "ReqMethod.CHAT_STEER" in source
    assert "handle_steer" in source
    # The steer branch must test the method, not a broader steer predicate.
    assert "_is_steer_message" not in source


# ------------------------------------------------------------------ history


@pytest.mark.anyio
async def test_accepted_steer_is_persisted_once_as_a_user_turn(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.append_history_record",
        lambda **kw: calls.append(kw),
    )
    handler = _make_handler(_fake_instance(_FakeResult(True, "steer_queued")))

    await handler.handle_steer(_req())

    assert len(calls) == 1
    record = calls[0]
    assert record["role"] == "user"
    assert record["content"] == "prefer the async client"
    assert record["session_id"] == "sess-1"
    assert record["extra"] == {"input_kind": "steer_queued"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        _FakeResult(False, "rejected", "no_active_round"),
        _FakeResult(False, "rejected", "interaction_terminated"),
    ],
)
async def test_rejected_steer_is_never_written_to_history(monkeypatch, result) -> None:
    """A rejection changed nothing, so history must not claim the user spoke.

    Otherwise reloading the session resurrects a message the agent never
    received, and the transcript disagrees with what actually happened.
    """
    calls: list[dict] = []
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.append_history_record",
        lambda **kw: calls.append(kw),
    )
    handler = _make_handler(_fake_instance(result))

    await handler.handle_steer(_req())

    assert calls == []


@pytest.mark.anyio
async def test_history_tag_follows_the_disposition_not_the_wire_method(
    monkeypatch,
) -> None:
    """Goal-supplement input must not be recorded as steering.

    The Web sends ordinary input over the steer path while a Goal is ACTIVE.
    When it lands as a follow-up it is not steering, and a "steer" tag on disk
    would outlive the request and mislead whoever reads the transcript.
    """
    calls: list[dict] = []
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.append_history_record",
        lambda **kw: calls.append(kw),
    )
    handler = _make_handler(_fake_instance(_FakeResult(True, "follow_up_queued")))

    await handler.handle_steer(_req())

    assert len(calls) == 1
    assert calls[0]["extra"] == {"input_kind": "follow_up_queued"}


@pytest.mark.anyio
async def test_empty_steer_is_not_written_to_history(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.append_history_record",
        lambda **kw: calls.append(kw),
    )
    handler = _make_handler(_fake_instance(_FakeResult(True, "steer_queued")))

    await handler.handle_steer(_req(""))

    assert calls == []
