# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""HITL resume should re-invoke skill_acceleration_exec so ReAct can summarize."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY
from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from jiuwenswarm.agents.harness.common.rails.stream_event_rail import (
    JiuSwarmStreamEventRail,
)
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter
from jiuwenswarm.server.runtime.skill_turbo.permission_bridge import (
    SKILL_TURBO_RESUME_CTX_KEY,
    load_resume_ctx,
    mark_resume_in_flight,
)
from jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools import (
    _SKILL_TURBO_HITL_PLACEHOLDER,
    _SKILL_TURBO_STOP_HINT,
    _resolve_skill_turbo_resume_session_id,
    _resume_user_input_from_raw,
    get_skill_turbo_resume_answers,
    reset_skill_turbo_resume_answers,
    set_skill_turbo_hitl_tic,
    set_skill_turbo_resume_answers,
)


class _StreamSession:
    def __init__(self):
        self.chunks = []

    async def write_stream(self, chunk):
        self.chunks.append(chunk)


def _tool_ctx(session, tool_name: str):
    from openjiuwen.core.single_agent.rail.base import ToolCallInputs

    tool_call = SimpleNamespace(id="call-1", name=tool_name, arguments={})
    return SimpleNamespace(
        session=session,
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name=tool_name,
            tool_args={},
            tool_result={"success": True},
        ),
        extra={},
        exception=None,
    )


def test_deep_agent_has_skill_turbo_interrupt():
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = None
    assert adapter._deep_agent_has_skill_turbo_interrupt() is False

    adapter._instance = SimpleNamespace(
        _loop_session=SimpleNamespace(
            get_state=lambda _key: SimpleNamespace(
                interrupted_tools={
                    "call_1": SimpleNamespace(
                        tool_call=SimpleNamespace(name="skill_acceleration_exec")
                    )
                }
            )
        )
    )
    assert adapter._deep_agent_has_skill_turbo_interrupt() is True

    adapter._instance._loop_session.get_state = lambda _key: SimpleNamespace(
        interrupted_tools={
            "call_1": SimpleNamespace(tool_call=SimpleNamespace(name="ask_user"))
        }
    )
    assert adapter._deep_agent_has_skill_turbo_interrupt() is False


@pytest.mark.asyncio
async def test_try_skill_turbo_resume_defers_when_outer_interrupt_and_no_ctx(monkeypatch):
    """Outer interrupt alone still defers when there is no SkillTurbo resume_ctx."""
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = SimpleNamespace(
        card=object(),
        _loop_session=SimpleNamespace(
            get_state=lambda _key: SimpleNamespace(
                interrupted_tools={
                    "call_1": SimpleNamespace(
                        tool_call=SimpleNamespace(name="skill_acceleration_exec")
                    )
                }
            )
        ),
    )
    request = AgentRequest(
        request_id="req-1",
        channel_id="officeclaw",
        session_id="sess-1",
        req_method=ReqMethod.CHAT_SEND,
        params={
            "answers": [{"question": "页数", "selected_options": ["10"]}],
            "source": "ask_user_interrupt",
            "request_id": "skill_turbo-tc-ask_user-1",
        },
    )

    class _Session:
        async def post_run(self):
            return None

        def update_state(self, _state):
            return None

    monkeypatch.setattr(
        "openjiuwen.core.session.agent.create_agent_session",
        lambda **_kwargs: _Session(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep._skill_turbo_set_agent_id",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep._skill_turbo_load_resume_ctx",
        lambda _session: _async_none(),
    )
    assert await adapter._try_skill_turbo_resume(request, {}) is None


async def _async_none():
    return None


async def _async_resume_ctx():
    return {"pending_tool_call_id": "skill_turbo-tc-ask_user-1", "plan_code": "x"}


@pytest.mark.asyncio
async def test_try_skill_turbo_resume_continues_with_outer_interrupt_when_ctx(
    monkeypatch,
):
    """Nested ask_user: resume_ctx present must not defer to DeepAgent."""
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = SimpleNamespace(
        card=object(),
        _loop_session=SimpleNamespace(
            get_state=lambda _key: SimpleNamespace(
                interrupted_tools={
                    "call_1": SimpleNamespace(
                        tool_call=SimpleNamespace(name="skill_acceleration_exec")
                    )
                }
            )
        ),
    )
    sentinel = object()
    adapter._make_skill_turbo_resume_stream = (
        lambda **_kwargs: sentinel  # type: ignore[method-assign]
    )
    request = AgentRequest(
        request_id="req-1",
        channel_id="officeclaw",
        session_id="sess-1",
        req_method=ReqMethod.CHAT_SEND,
        params={
            "answers": [{"question": "页数", "selected_options": ["10"]}],
            "source": "ask_user_interrupt",
            "request_id": "skill_turbo-tc-ask_user-1",
        },
    )

    class _Session:
        async def post_run(self):
            return None

        def update_state(self, _state):
            return None

    monkeypatch.setattr(
        "openjiuwen.core.session.agent.create_agent_session",
        lambda **_kwargs: _Session(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep._skill_turbo_set_agent_id",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep._skill_turbo_load_resume_ctx",
        lambda _session: _async_resume_ctx(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep._skill_turbo_mark_resume_in_flight",
        lambda _session, _ctx: _async_none(),
    )
    assert await adapter._try_skill_turbo_resume(request, {}) is sentinel


async def _async_in_flight_resume_ctx():
    return {
        "pending_tool_call_id": "skill_turbo-tc-ask_user-1",
        "plan_code": "x",
        "resume_in_flight": True,
    }


@pytest.mark.asyncio
async def test_try_skill_turbo_resume_ignores_duplicate_while_in_flight(monkeypatch):
    """Second answer submit while resume is running must not start another stream."""
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = SimpleNamespace(card=object(), _loop_session=None)
    called = {"resume_stream": False}

    def _should_not_run(**_kwargs):
        called["resume_stream"] = True
        raise AssertionError("duplicate resume must not start resume_stream")

    adapter._make_skill_turbo_resume_stream = _should_not_run  # type: ignore[method-assign]
    request = AgentRequest(
        request_id="req-dup",
        channel_id="officeclaw",
        session_id="sess-1",
        req_method=ReqMethod.CHAT_SEND,
        params={
            "answers": [{"question": "页数", "selected_options": ["10"]}],
            "source": "ask_user_interrupt",
            "request_id": "skill_turbo-tc-ask_user-1",
        },
    )

    class _Session:
        async def post_run(self):
            return None

        def update_state(self, _state):
            return None

    monkeypatch.setattr(
        "openjiuwen.core.session.agent.create_agent_session",
        lambda **_kwargs: _Session(),
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep._skill_turbo_set_agent_id",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep._skill_turbo_load_resume_ctx",
        lambda _session: _async_in_flight_resume_ctx(),
    )
    stream = await adapter._try_skill_turbo_resume(request, {})
    assert stream is not None
    chunks = [chunk async for chunk in stream]
    assert len(chunks) == 1
    assert chunks[0].is_complete is True
    assert chunks[0].payload is None
    assert called["resume_stream"] is False


@pytest.mark.asyncio
async def test_mark_resume_in_flight_persists_across_sessions():
    """in-flight flag must survive post_run so a second session load sees it."""
    checkpoint: dict = {}

    class _CheckpointSession:
        """Mirrors SDK Session: post_run is one-shot; commit is repeatable."""

        def __init__(self):
            self._state: dict = {}
            self._post_run_done = False

        async def pre_run(self, inputs=None):
            self._state = copy.deepcopy(checkpoint)

        async def commit(self):
            checkpoint.clear()
            checkpoint.update(copy.deepcopy(self._state))

        async def post_run(self):
            if self._post_run_done:
                return self
            await self.commit()
            self._post_run_done = True
            return self

        def update_state(self, mapping):
            self._state.update(mapping)

        def get_state(self, key):
            return self._state.get(key)

    ctx = {
        "plan_code": "plan-x",
        "pending_tool_call_id": "skill_turbo-tc-ask_user-1",
        "inputs": {},
    }
    writer = _CheckpointSession()
    await writer.pre_run()
    writer.update_state({SKILL_TURBO_RESUME_CTX_KEY: ctx})
    await writer.post_run()

    marker = _CheckpointSession()
    loaded = await load_resume_ctx(marker)
    assert loaded is not None
    assert loaded.get("resume_in_flight") is not True
    await mark_resume_in_flight(marker, loaded)

    reader = _CheckpointSession()
    again = await load_resume_ctx(reader)
    assert again is not None
    assert again.get("resume_in_flight") is True
    assert again.get("pending_tool_call_id") == "skill_turbo-tc-ask_user-1"


@pytest.mark.asyncio
async def test_clear_resume_in_flight_persists_after_same_session_post_run():
    """clear must commit even when mark already post_run'd the same Session.

    Production SDK Session.post_run is one-shot (_post_run_done). Resume marks
    via post_run, then finally clears on the same instance — clear must use
    commit (or equivalent) so the unlocked flag reaches checkpointer.
    """
    checkpoint: dict = {}

    class _CheckpointSession:
        def __init__(self):
            self._state: dict = {}
            self._pre_run_done = False
            self._post_run_done = False
            self.commit_calls = 0
            self.post_run_calls = 0

        async def pre_run(self, inputs=None):
            if self._pre_run_done:
                return
            self._state = copy.deepcopy(checkpoint)
            self._pre_run_done = True

        async def reload_from_checkpointer(self, inputs=None):
            self._pre_run_done = False
            await self.pre_run(inputs=inputs)

        async def commit(self):
            self.commit_calls += 1
            checkpoint.clear()
            checkpoint.update(copy.deepcopy(self._state))

        async def post_run(self):
            self.post_run_calls += 1
            if self._post_run_done:
                return self
            await self.commit()
            self._post_run_done = True
            return self

        def update_state(self, mapping):
            self._state.update(mapping)

        def get_state(self, key):
            return self._state.get(key)

    from jiuwenswarm.server.runtime.skill_turbo.permission_bridge import (
        clear_resume_in_flight,
    )

    session = _CheckpointSession()
    await session.pre_run()
    await mark_resume_in_flight(
        session,
        {
            "plan_code": "plan-x",
            "pending_tool_call_id": "skill_turbo-tc-ask_user-1",
            "inputs": {},
        },
    )
    assert session._post_run_done is True
    assert checkpoint.get(SKILL_TURBO_RESUME_CTX_KEY, {}).get("resume_in_flight") is True

    await clear_resume_in_flight(session)
    assert session.commit_calls >= 2  # mark's post_run + clear's commit
    assert session._state[SKILL_TURBO_RESUME_CTX_KEY]["resume_in_flight"] is False

    loaded = await load_resume_ctx(_CheckpointSession())
    assert loaded is not None
    assert loaded.get("resume_in_flight") is not True
    assert loaded.get("pending_tool_call_id") == "skill_turbo-tc-ask_user-1"


@pytest.mark.asyncio
async def test_clear_resume_in_flight_does_not_clobber_nested_save_from_other_session():
    """Facade clear must not overwrite executor's nested HITL pending tcid.

    Production: S1 (interface_deep mark) keeps audience tcid in memory; S2
    (executor) save_resume_ctx commits style tcid. HITL abort finally calls
    clear_resume_in_flight(S1) — without reloading checkpointer first, S1 would
    commit the stale audience pending and re-pop the same ask cards.
    """
    checkpoint: dict = {}

    class _CheckpointSession:
        def __init__(self):
            self._state: dict = {}
            self._pre_run_done = False
            self._post_run_done = False
            self.commit_calls = 0

        async def pre_run(self, inputs=None):
            if self._pre_run_done:
                return
            self._state = copy.deepcopy(checkpoint)
            self._pre_run_done = True

        async def reload_from_checkpointer(self, inputs=None):
            self._pre_run_done = False
            await self.pre_run(inputs=inputs)

        async def commit(self):
            self.commit_calls += 1
            checkpoint.clear()
            checkpoint.update(copy.deepcopy(self._state))

        async def post_run(self):
            if self._post_run_done:
                return self
            await self.commit()
            self._post_run_done = True
            return self

        def update_state(self, mapping):
            self._state.update(mapping)

        def get_state(self, key):
            return self._state.get(key)

    from jiuwenswarm.server.runtime.skill_turbo.permission_bridge import (
        clear_resume_in_flight,
        save_resume_ctx,
    )

    audience = "skill_turbo-tc-ask_user-762c356d-0"
    style = "skill_turbo-tc-ask_user-18cefd55-0"

    s1 = _CheckpointSession()
    await s1.pre_run()
    await mark_resume_in_flight(
        s1,
        {
            "plan_code": "plan-audience",
            "pending_tool_call_id": audience,
            "inputs": {"q": "audience"},
        },
    )
    assert checkpoint[SKILL_TURBO_RESUME_CTX_KEY]["pending_tool_call_id"] == audience
    assert s1._state[SKILL_TURBO_RESUME_CTX_KEY]["pending_tool_call_id"] == audience

    s2 = _CheckpointSession()
    await s2.pre_run()
    await save_resume_ctx(
        s2,
        plan_code="plan-style",
        inputs={"q": "style"},
        pending_tool_call_id=style,
    )
    assert checkpoint[SKILL_TURBO_RESUME_CTX_KEY]["pending_tool_call_id"] == style
    assert checkpoint[SKILL_TURBO_RESUME_CTX_KEY]["resume_in_flight"] is False
    # S1 memory still holds the stale audience mark
    assert s1._state[SKILL_TURBO_RESUME_CTX_KEY]["pending_tool_call_id"] == audience

    commits_before_clear = s1.commit_calls
    await clear_resume_in_flight(s1)
    # Nested save already cleared in_flight — clear must not re-commit S1 stale memory
    assert s1.commit_calls == commits_before_clear
    assert checkpoint[SKILL_TURBO_RESUME_CTX_KEY]["pending_tool_call_id"] == style
    assert checkpoint[SKILL_TURBO_RESUME_CTX_KEY]["resume_in_flight"] is False

    loaded = await load_resume_ctx(_CheckpointSession())
    assert loaded is not None
    assert loaded.get("pending_tool_call_id") == style
    assert loaded.get("resume_in_flight") is not True


@pytest.mark.asyncio
async def test_save_resume_ctx_persists_nested_tcid_after_same_session_post_run():
    """Nested ask_user must persist the new pending tcid after mark's post_run.

    Without commit(), save_resume_ctx's post_run is a no-op on the resume Session,
    so the next answer still loads the old audience tcid and re-pops the same cards.
    """
    checkpoint: dict = {}

    class _CheckpointSession:
        def __init__(self):
            self._state: dict = {}
            self._post_run_done = False
            self.commit_calls = 0

        async def pre_run(self, inputs=None):
            self._state = copy.deepcopy(checkpoint)

        async def commit(self):
            self.commit_calls += 1
            checkpoint.clear()
            checkpoint.update(copy.deepcopy(self._state))

        async def post_run(self):
            if self._post_run_done:
                return self
            await self.commit()
            self._post_run_done = True
            return self

        def update_state(self, mapping):
            self._state.update(mapping)

        def get_state(self, key):
            return self._state.get(key)

    from jiuwenswarm.server.runtime.skill_turbo.permission_bridge import save_resume_ctx

    session = _CheckpointSession()
    await session.pre_run()
    await mark_resume_in_flight(
        session,
        {
            "plan_code": "plan-x",
            "pending_tool_call_id": "skill_turbo-tc-ask_user-762c356d-0",
            "inputs": {"topic": "杭州旅游"},
        },
    )
    assert session._post_run_done is True
    assert (
        checkpoint[SKILL_TURBO_RESUME_CTX_KEY]["pending_tool_call_id"]
        == "skill_turbo-tc-ask_user-762c356d-0"
    )

    commits_before_nested = session.commit_calls
    await save_resume_ctx(
        session,
        plan_code="plan-x",
        inputs={"topic": "杭州旅游"},
        pending_tool_call_id="skill_turbo-tc-ask_user-18cefd55-0",
        task_states=[{"id": "t1", "status": "in_progress"}],
    )
    assert session.commit_calls > commits_before_nested
    assert (
        checkpoint[SKILL_TURBO_RESUME_CTX_KEY]["pending_tool_call_id"]
        == "skill_turbo-tc-ask_user-18cefd55-0"
    )
    assert checkpoint[SKILL_TURBO_RESUME_CTX_KEY].get("resume_in_flight") is not True

    loaded = await load_resume_ctx(_CheckpointSession())
    assert loaded is not None
    assert loaded.get("pending_tool_call_id") == "skill_turbo-tc-ask_user-18cefd55-0"


@pytest.mark.asyncio
async def test_clear_resume_ctx_persists_after_same_session_post_run():
    """clear_resume_ctx must commit after mark's one-shot post_run."""
    checkpoint: dict = {}

    class _CheckpointSession:
        def __init__(self):
            self._state: dict = {}
            self._post_run_done = False
            self.commit_calls = 0

        async def pre_run(self, inputs=None):
            self._state = copy.deepcopy(checkpoint)

        async def commit(self):
            self.commit_calls += 1
            checkpoint.clear()
            checkpoint.update(copy.deepcopy(self._state))

        async def post_run(self):
            if self._post_run_done:
                return self
            await self.commit()
            self._post_run_done = True
            return self

        def update_state(self, mapping):
            self._state.update(mapping)

        def get_state(self, key):
            return self._state.get(key)

    from jiuwenswarm.server.runtime.skill_turbo.permission_bridge import clear_resume_ctx

    session = _CheckpointSession()
    await session.pre_run()
    await mark_resume_in_flight(
        session,
        {
            "plan_code": "plan-x",
            "pending_tool_call_id": "skill_turbo-tc-ask_user-1",
            "inputs": {},
        },
    )
    assert SKILL_TURBO_RESUME_CTX_KEY in checkpoint

    await clear_resume_ctx(session)
    # Caller-style second post_run must not be required for persistence.
    await session.post_run()
    assert checkpoint.get(SKILL_TURBO_RESUME_CTX_KEY) in (None, {})
    assert await load_resume_ctx(_CheckpointSession()) is None


@pytest.mark.asyncio
async def test_save_resume_ctx_clears_stale_in_flight_flag_on_nested_hitl():
    """Nested ask_user save must not keep the prior resume's in_flight flag.

    Deep-merge ``update_state`` would otherwise leave resume_in_flight=True on
    the new pending tcid and the user's next answer becomes a duplicate no-op.
    """
    checkpoint: dict = {}

    class _MergingCheckpointSession:
        def __init__(self):
            self._state: dict = {}
            self._post_run_done = False

        async def pre_run(self, inputs=None):
            self._state = copy.deepcopy(checkpoint)

        async def commit(self):
            checkpoint.clear()
            checkpoint.update(copy.deepcopy(self._state))

        async def post_run(self):
            if self._post_run_done:
                return self
            await self.commit()
            self._post_run_done = True
            return self

        def update_state(self, mapping):
            for key, value in mapping.items():
                existing = self._state.get(key)
                if isinstance(existing, dict) and isinstance(value, dict):
                    self._state[key] = {**existing, **value}
                else:
                    self._state[key] = value

        def get_state(self, key):
            return self._state.get(key)

    from jiuwenswarm.server.runtime.skill_turbo.permission_bridge import save_resume_ctx

    first = _MergingCheckpointSession()
    await first.pre_run()
    first.update_state(
        {
            SKILL_TURBO_RESUME_CTX_KEY: {
                "plan_code": "plan-x",
                "pending_tool_call_id": "skill_turbo-tc-ask_user-1",
                "inputs": {},
                "resume_in_flight": True,
            }
        }
    )
    await first.post_run()

    nested = _MergingCheckpointSession()
    await save_resume_ctx(
        nested,
        plan_code="plan-x",
        inputs={"topic": "hangzhou"},
        pending_tool_call_id="skill_turbo-tc-ask_user-2",
        task_states=[{"id": "t1", "status": "in_progress"}],
    )

    loaded = await load_resume_ctx(_MergingCheckpointSession())
    assert loaded is not None
    assert loaded.get("pending_tool_call_id") == "skill_turbo-tc-ask_user-2"
    assert loaded.get("resume_in_flight") is not True


@pytest.mark.asyncio
async def test_emit_skill_turbo_hitl_keeps_pending_tool_call_request_id(monkeypatch):
    """HITL card request_id must stay skill_turbo-tc-* (not HTTP request id)."""
    pending_tcid = "skill_turbo-tc-ask_user-9"
    http_rid = "http-req-abc"
    tool_call = SimpleNamespace(
        id=pending_tcid,
        name="ask_user",
        arguments={"questions": [{"question": "页数"}]},
    )
    tic = SimpleNamespace(tool_call=tool_call, request=SimpleNamespace())

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep._skill_turbo_extract_tool_interrupt",
        lambda _exc: tic,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep._skill_turbo_build_interaction_output",
        lambda _exc: SimpleNamespace(payload={"id": pending_tcid}),
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.convert_interactions_to_ask_user_question",
        lambda _items: {
            "event_type": "chat.ask_user_question",
            "request_id": pending_tcid,
            "questions": [{"question": "页数"}],
            "source": "ask_user_interrupt",
        },
    )

    request = AgentRequest(
        request_id=http_rid,
        channel_id="officeclaw",
        session_id="sess-1",
        req_method=ReqMethod.CHAT_SEND,
        params={},
    )
    chunks = [
        chunk
        async for chunk in JiuWenSwarmDeepAdapter._emit_skill_turbo_hitl_chunks(
            request, RuntimeError("abort")
        )
    ]
    ask = next(
        c
        for c in chunks
        if isinstance(c.payload, dict)
        and c.payload.get("event_type") == "chat.ask_user_question"
    )
    assert ask.payload["request_id"] == pending_tcid
    assert ask.request_id == http_rid


def test_resume_user_input_from_interactive_input():
    interactive = InteractiveInput()
    payload = {
        "status": "answered",
        "answers": [
            {"question": "受众", "selected_options": ["企业高管"]},
            {"question": "目的", "selected_options": ["工作汇报"]},
        ],
    }
    interactive.update("call_outer", payload)
    got = _resume_user_input_from_raw(interactive, {}, None)
    assert got is payload
    assert len(got["answers"]) == 2


def test_resume_user_input_from_raw_answers_list():
    answers = [{"question": "页数", "selected_options": ["10"]}]
    adapter = SimpleNamespace(
        _skill_turbo_answers_to_confirm_payload=lambda raw, _ctx: raw
    )
    assert _resume_user_input_from_raw(answers, {}, adapter) == answers


def test_resume_answers_contextvar_roundtrip():
    token = set_skill_turbo_resume_answers(["a"])
    try:
        assert get_skill_turbo_resume_answers() == ["a"]
    finally:
        reset_skill_turbo_resume_answers(token)
    assert get_skill_turbo_resume_answers() is None


def test_resolve_skill_turbo_resume_session_id_prefers_metadata():
    parent = SimpleNamespace(get_session_id=lambda: "parent-sid")
    assert _resolve_skill_turbo_resume_session_id("meta-sid", parent) == "meta-sid"


def test_resolve_skill_turbo_resume_session_id_falls_back_to_parent():
    parent = SimpleNamespace(get_session_id=lambda: "parent-sid")
    assert _resolve_skill_turbo_resume_session_id("", parent) == "parent-sid"
    assert _resolve_skill_turbo_resume_session_id(None, parent) == "parent-sid"
    assert _resolve_skill_turbo_resume_session_id("", None) == ""


@pytest.mark.asyncio
async def test_skill_acceleration_exec_resume_skips_duplicate_tool_call_emit():
    rail = JiuSwarmStreamEventRail()
    session = _StreamSession()
    ctx = _tool_ctx(session, "skill_acceleration_exec")
    ctx.extra[RESUME_USER_INPUT_KEY] = [{"question": "q", "selected_options": ["a"]}]
    await rail.before_tool_call(ctx)
    assert not any(getattr(chunk, "type", None) == "tool_call" for chunk in session.chunks)
    assert not any(getattr(chunk, "type", None) == "tool_update" for chunk in session.chunks)
    await rail.after_tool_call(ctx)


@pytest.mark.asyncio
async def test_skill_acceleration_exec_first_invoke_still_emits_tool_call():
    rail = JiuSwarmStreamEventRail()
    session = _StreamSession()
    ctx = _tool_ctx(session, "skill_acceleration_exec")
    await rail.before_tool_call(ctx)
    assert any(getattr(chunk, "type", None) == "tool_call" for chunk in session.chunks)
    await rail.after_tool_call(ctx)


def _ask_user_request(source: str) -> AgentRequest:
    return AgentRequest(
        request_id="req-1",
        channel_id="officeclaw",
        session_id="sess-1",
        req_method=ReqMethod.CHAT_SEND,
        params={
            "answers": [{"selected_options": ["本次允许"]}],
            "source": source,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["permission_interrupt", "confirm_interrupt", ""])
async def test_try_skill_turbo_resume_ignores_non_ask_user_answers(source):
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = SimpleNamespace(card=object(), _loop_session=None)
    assert await adapter._try_skill_turbo_resume(_ask_user_request(source), {}) is None


def test_hitl_placeholder_is_recognized_by_context_repair():
    rail = JiuSwarmStreamEventRail()
    tool_call_id = "call_982c"
    placeholders = {
        tool_call_id: rail._tool_interrupted_message("skill_acceleration_exec"),
    }
    names = {tool_call_id: "skill_acceleration_exec"}
    leaked = ToolMessage(
        content="{'success': False, 'error': '任务已暂停等待审批'}",
        tool_call_id=tool_call_id,
    )
    placeholder = ToolMessage(
        content=_SKILL_TURBO_HITL_PLACEHOLDER,
        tool_call_id=tool_call_id,
    )
    assert rail._is_tool_interrupt_placeholder(leaked, placeholders, names) is False
    assert rail._is_tool_interrupt_placeholder(placeholder, placeholders, names) is True


@pytest.mark.asyncio
async def test_skill_turbo_hitl_after_tool_call_writes_placeholder_tool_msg():
    rail = JiuSwarmStreamEventRail()
    rail.set_skill_turbo_adapter(object())
    session = _StreamSession()
    tool_call = SimpleNamespace(
        id="call_982c",
        name="skill_acceleration_exec",
        arguments={"query": "生成PPT"},
    )
    leaked = ToolMessage(
        content="{'success': False, 'error': '任务已暂停等待审批'}",
        tool_call_id="call_982c",
    )
    ctx = SimpleNamespace(
        session=session,
        inputs=ToolCallInputs(
            tool_call=tool_call,
            tool_name="skill_acceleration_exec",
            tool_args={"query": "生成PPT"},
            tool_result={"success": False, "error": "任务已暂停等待审批"},
            tool_msg=leaked,
        ),
        extra={},
        exception=None,
        request_force_finish=lambda *_args, **_kwargs: None,
    )
    inner_tc = SimpleNamespace(
        id="skill_turbo-tc-ask_user-1",
        name="ask_user",
        arguments={"questions": [{"question": "风格", "options": [{"label": "科技极简"}]}]},
    )
    tic = SimpleNamespace(
        request=SimpleNamespace(message="ask", tool_call_id="skill_turbo-tc-ask_user-1"),
        tool_call=inner_tc,
    )
    set_skill_turbo_hitl_tic(tic)
    try:
        await rail.after_tool_call(ctx)
    finally:
        set_skill_turbo_hitl_tic(None)

    assert isinstance(ctx.inputs.tool_msg, ToolMessage)
    assert ctx.inputs.tool_msg.content == rail._tool_interrupted_message(
        "skill_acceleration_exec"
    )
    assert ctx.inputs.tool_msg.tool_call_id == "call_982c"
    assert ctx.inputs.tool_msg is not leaked


class _ModelContext:
    def __init__(self, messages):
        self.messages = list(messages)

    def get_messages(self):
        return list(self.messages)

    def pop_messages(self, size):
        popped = self.messages[:size]
        self.messages = self.messages[size:]
        return popped

    async def add_messages(self, message):
        self.messages.append(message)


@pytest.mark.asyncio
async def test_fix_incomplete_tool_context_keeps_stop_hint_over_hitl_placeholder():
    rail = JiuSwarmStreamEventRail()
    tool_call_id = "call_982c"
    stop_hint_msg = ToolMessage(
        content="任务已完成" + _SKILL_TURBO_STOP_HINT,
        tool_call_id=tool_call_id,
    )
    ctx = SimpleNamespace(
        context=_ModelContext([
            UserMessage(content="生成一页PPT"),
            AssistantMessage(
                content="",
                tool_calls=[{
                    "type": "function",
                    "id": tool_call_id,
                    "function": {
                        "name": "skill_acceleration_exec",
                        "arguments": "{\"query\":\"生成PPT\"}",
                    },
                }],
            ),
            ToolMessage(
                content=_SKILL_TURBO_HITL_PLACEHOLDER,
                tool_call_id=tool_call_id,
            ),
            ToolMessage(
                content=_SKILL_TURBO_HITL_PLACEHOLDER,
                tool_call_id=tool_call_id,
            ),
            stop_hint_msg,
        ]),
        inputs=SimpleNamespace(tools=[]),
        session=None,
        extra={},
    )

    await rail._fix_incomplete_tool_context(ctx)

    messages = ctx.context.get_messages()
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == tool_call_id
    assert "任务已暂停等待审批" not in tool_msgs[0].content
    assert _SKILL_TURBO_HITL_PLACEHOLDER not in tool_msgs[0].content
    assert "The skill_acceleration_exec task is complete" in tool_msgs[0].content
    assert "skill_tool" in tool_msgs[0].content
