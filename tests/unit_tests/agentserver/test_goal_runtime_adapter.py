"""Tests for the Goal capability adapter used by JiuwenSwarm."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Goal capability tests exercise the checked-out OpenJiuwen implementation,
# not whichever released package happens to be installed in the test venv.
_AGENT_CORE_ROOT = Path(__file__).resolve().parents[3].parent / "agent-core"
if str(_AGENT_CORE_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_CORE_ROOT))

from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.harness.goal.schema import GoalOperationError, GoalRecord, GoalStatus
from openjiuwen.harness.schema.interaction import InteractionEventType

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime.agent_adapter.interface import (
    JiuWenSwarm,
    _should_record_user_history,
)
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


class _FakeGoals:
    def __init__(self) -> None:
        self.record: GoalRecord | None = None
        self.calls: list[tuple[str, object]] = []

    async def get(self) -> GoalRecord | None:
        self.calls.append(("get", None))
        return self.record

    def get_store(self):
        return SimpleNamespace(load=lambda: self.record)

    async def set(
        self,
        objective: str,
        *,
        overwrite_confirmed: bool = False,
        token_budget: int | None = None,
        max_attempts: int | None = None,
    ) -> GoalRecord:
        self.calls.append(("set", objective))
        if not objective.strip():
            raise GoalOperationError(
                operation="set",
                code="invalid_objective",
                message="goal objective must not be empty",
            )
        if self.record is not None and not overwrite_confirmed:
            raise GoalOperationError(
                operation="set",
                code="already_exists",
                message="a goal already exists for this session",
                goal=self.record,
            )
        self.record = GoalRecord.create(session_id="session-1", objective=objective)
        return self.record

    async def pause(self) -> GoalRecord | None:
        self.calls.append(("pause", None))
        if self.record is not None:
            self.record.status = GoalStatus.PAUSED
        return self.record

    async def resume(self) -> GoalRecord | None:
        self.calls.append(("resume", None))
        if self.record is not None and self.record.status is GoalStatus.PAUSED:
            self.record.status = GoalStatus.ACTIVE
        return self.record

    async def clear(self) -> GoalRecord | None:
        self.calls.append(("clear", None))
        removed = self.record
        self.record = None
        return removed


def _adapter(goal_manager: _FakeGoals) -> JiuWenSwarmDeepAdapter:
    adapter = JiuWenSwarmDeepAdapter.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = SimpleNamespace(goal_manager=goal_manager)
    adapter._is_session_scoped_adapter = True
    return adapter


class _FakeGoalAdapter:
    async def handle_goal_command_structured(
        self,
        params: dict[str, object],
        session_id: str,
    ) -> dict[str, object]:
        return {
            "result_type": "goal_control",
            "action": params.get("action", "get"),
            "goal": None,
            "output": "No goal in this session.",
        }


class _FakeSessionManager:
    def get_session_id(self, session_id: str | None) -> str:
        return session_id or "default"


@pytest.mark.asyncio
async def test_structured_set_calls_goal_capability_and_returns_stream_intent() -> None:
    goals = _FakeGoals()
    result = await _adapter(goals).handle_goal_command_structured(
        {"action": "set", "objective": "ship the feature"},
        session_id="session-1",
    )

    assert goals.calls == [("set", "ship the feature")]
    assert result is not None
    assert result["result_type"] == "goal_stream"
    assert result["goal"]["objective"] == "ship the feature"


@pytest.mark.asyncio
async def test_resume_without_goal_is_a_normal_control_response() -> None:
    result = await _adapter(_FakeGoals()).handle_goal_command_structured(
        {"action": "resume"},
        session_id="session-1",
    )

    assert result is not None
    assert result["result_type"] == "goal_control"
    assert result["goal"] is None
    assert result["output"] == "No goal in this session."


@pytest.mark.asyncio
async def test_set_existing_goal_returns_confirmation_data() -> None:
    goals = _FakeGoals()
    goals.record = GoalRecord.create(session_id="session-1", objective="old goal")

    result = await _adapter(goals).handle_goal_command_structured(
        {"action": "set", "objective": "new goal"},
        session_id="session-1",
    )

    assert result is not None
    assert result["result_type"] == "goal_confirm_required"
    assert result["existing_goal"]["objective"] == "old goal"
    assert result["requested_objective"] == "new goal"


@pytest.mark.asyncio
async def test_slash_goal_parser_uses_set_argument_or_bare_objective() -> None:
    goals = _FakeGoals()
    adapter = _adapter(goals)

    explicit = await adapter._handle_goal_slash_command("/goal set write a report")
    bare = await adapter._handle_goal_slash_command("/goal write a report")
    empty = await adapter._handle_goal_slash_command("/goal set")

    assert explicit is not None
    assert explicit["goal"]["objective"] == "write a report"
    assert bare is not None
    assert bare["result_type"] == "goal_confirm_required"
    assert empty is not None
    assert empty["result_type"] == "goal_error"
    assert empty["error_code"] == "invalid_objective"


def test_runtime_events_are_adapted_without_leaking_runtime_objects() -> None:
    goal = GoalRecord.create(session_id="session-1", objective="ship the feature").to_dict()
    updated = JiuWenSwarmDeepAdapter._parse_stream_chunk(
        OutputSchema(
            type=InteractionEventType.GOAL_UPDATED.value,
            index=0,
            payload={"goal": goal},
        )
    )
    failed = JiuWenSwarmDeepAdapter._parse_stream_chunk(
        OutputSchema(
            type=InteractionEventType.EXECUTION_ERROR.value,
            index=0,
            payload={"code": "round_execution_error", "message": "round failed"},
        )
    )

    assert updated == {"event_type": "goal.updated", "goal": goal}
    assert failed == {
        "event_type": "execution.error",
        "code": "round_execution_error",
        "message": "round failed",
        "goal": None,
    }


def test_runtime_goal_update_payload_is_always_nested_under_goal() -> None:
    goal = GoalRecord.create(session_id="session-1", objective="ship the feature").to_dict()
    updated = JiuWenSwarmDeepAdapter._parse_stream_chunk(
        {
            "type": InteractionEventType.GOAL_UPDATED.value,
            "payload": goal,
        }
    )
    cleared = JiuWenSwarmDeepAdapter._parse_stream_chunk(
        {
            "type": InteractionEventType.GOAL_UPDATED.value,
            "payload": {"goal": None},
        }
    )

    assert updated == {"event_type": "goal.updated", "goal": goal}
    assert cleared == {"event_type": "goal.updated", "goal": None}


@pytest.mark.asyncio
async def test_command_goal_unary_response_stays_rpc_payload() -> None:
    facade = JiuWenSwarm.__new__(JiuWenSwarm)
    facade._adapter = _FakeGoalAdapter()
    facade._session_manager = _FakeSessionManager()

    response = await facade.process_message(
        AgentRequest(
            request_id="goal-get-1",
            channel_id="tui",
            session_id="session-1",
            req_method=ReqMethod.COMMAND_GOAL,
            params={"action": "get"},
        )
    )

    assert response.ok is True
    assert response.payload is not None
    assert "event_type" not in response.payload
    assert response.payload["record"] is None
    assert response.payload["message"] == "No goal in this session."


def test_active_goal_demotes_intermediate_chat_final_to_delta() -> None:
    goals = _FakeGoals()
    goals.record = GoalRecord.create(session_id="session-1", objective="ship the feature")
    adapter = _adapter(goals)

    payload = adapter._adapt_goal_intermediate_final(
        {"event_type": "chat.final", "content": "attempt output"}
    )

    assert payload == {
        "event_type": "chat.delta",
        "content": "attempt output",
        "goal_intermediate": True,
    }


def test_internal_goal_resume_is_not_recorded_as_user_history() -> None:
    assert _should_record_user_history(
        {
            "query": "/goal resume",
            "runtime_mode": "follow_up",
            "log_as_user": False,
        }
    ) is False
    assert _should_record_user_history({"query": "hello"}) is True


def test_completed_goal_keeps_chat_final_terminal() -> None:
    goals = _FakeGoals()
    goals.record = GoalRecord.create(session_id="session-1", objective="ship the feature")
    goals.record.status = GoalStatus.COMPLETED
    adapter = _adapter(goals)

    payload = adapter._adapt_goal_intermediate_final(
        {"event_type": "chat.final", "content": "final output"}
    )

    assert payload == {"event_type": "chat.final", "content": "final output"}


def test_structured_command_goal_set_maps_to_pending_op() -> None:
    req = AgentRequest(
        request_id="g1",
        channel_id="web",
        session_id="session-1",
        req_method=ReqMethod.COMMAND_GOAL,
        params={
            "action": "set",
            "objective": "ship it",
            "overwrite_confirmed": True,
        },
    )
    op = JiuWenSwarmDeepAdapter._structured_goal_op_from_request(req)
    assert op == {
        "action": "set",
        "objective": "ship it",
        "overwrite_confirmed": True,
    }


def test_chat_send_does_not_map_to_structured_goal_op() -> None:
    req = AgentRequest(
        request_id="c1",
        channel_id="web",
        session_id="session-1",
        req_method=ReqMethod.CHAT_SEND,
        params={"query": "/goal set do not parse on web", "mode": "agent"},
    )
    assert JiuWenSwarmDeepAdapter._structured_goal_op_from_request(req) is None


def test_web_channel_does_not_treat_goal_slash_as_intent() -> None:
    assert (
        JiuWenSwarmDeepAdapter._parse_goal_slash_intent("/goal set write a report")
        == {"action": "set", "objective": "write a report"}
    )
    # Parsing exists, but stream path only applies it when channel_id == "tui".
    # Web must use command.goal; this documents the contract for callers.
    req = AgentRequest(
        request_id="w1",
        channel_id="web",
        session_id="session-1",
        req_method=ReqMethod.CHAT_SEND,
        params={"query": "/goal set write a report"},
    )
    assert JiuWenSwarmDeepAdapter._structured_goal_op_from_request(req) is None
