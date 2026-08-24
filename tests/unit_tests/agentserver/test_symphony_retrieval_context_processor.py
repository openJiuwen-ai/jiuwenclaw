import json

import pytest
from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.foundation.llm import AssistantMessage, ToolCall, ToolMessage, UserMessage

from jiuwenswarm.agents.harness.common.rails.symphony.retrieval_context_processor import (
    SymphonyRetrievalCompactProcessor,
    SymphonyRetrievalCompactProcessorConfig,
)


def _assistant_tool_call(call_id: str, tool_name: str) -> AssistantMessage:
    return AssistantMessage(
        tool_calls=[
            ToolCall(
                id=call_id,
                type="function",
                name=tool_name,
                arguments="{}",
            )
        ]
    )


def _tool_result(call_id: str, payload: object, *, as_json: bool = False) -> ToolMessage:
    content = json.dumps(payload, ensure_ascii=False) if as_json else str(payload)
    return ToolMessage(content=content, tool_call_id=call_id)


def _ready_plan(*skill_ids: str, edges: list[dict] | None = None) -> dict:
    return {
        "success": True,
        "planned_graph": {
            "graph": {
                "id": "plan-1",
                "type": "planned_graph",
                "directed": True,
                "metadata": {"status": "ready"},
                "nodes": {
                    skill_id: {
                        "label": skill_id,
                        "metadata": {"type": "skill"},
                    }
                    for skill_id in skill_ids
                },
                "edges": edges or [],
            }
        },
    }


def _large_explore_result(*worker_ids: str) -> dict:
    return {
        "success": True,
        "skill_tree": {
            "candidates": [{"worker_id": worker_id} for worker_id in worker_ids],
        },
        "padding": "x" * 1800,
    }


async def _project(messages: list) -> tuple[object, ContextWindow]:
    processor = SymphonyRetrievalCompactProcessor(
        SymphonyRetrievalCompactProcessorConfig()
    )
    window = ContextWindow(context_messages=messages)
    event, projected = await processor.on_get_context_window(None, window)
    return event, projected


@pytest.mark.asyncio
@pytest.mark.parametrize("as_json", [False, True])
async def test_compacts_python_dict_and_json_compose_results(as_json: bool) -> None:
    messages = [
        UserMessage(content="compose a workflow"),
        _assistant_tool_call("explore", "skill_branch_explore"),
        _tool_result("explore", _large_explore_result("skill-a")),
        _assistant_tool_call("compose", "symphony_compose_graph"),
        _tool_result("compose", _ready_plan("skill-a"), as_json=as_json),
    ]

    event, projected = await _project(messages)

    assert event is not None
    assert event.messages_to_modify == [2]
    summary = json.loads(projected.context_messages[2].content)
    assert summary == {
        "success": True,
        "compacted": True,
        "reason": "retrieval_consumed_by_executable_plan",
        "selected_skill_ids": ["skill-a"],
        "candidate_count": 1,
    }
    assert projected.context_messages[2].tool_call_id == "explore"
    assert projected.context_messages[4].tool_call_id == "compose"
    assert messages[2].content == str(_large_explore_result("skill-a"))


@pytest.mark.asyncio
async def test_compacts_multiple_retrievals_but_not_results_after_non_ready_compose() -> None:
    messages = [
        UserMessage(content="compose a workflow"),
        _assistant_tool_call("explore", "skill_branch_explore"),
        _tool_result("explore", _large_explore_result("skill-a")),
        _assistant_tool_call("peek", "skill_branch_peek"),
        _tool_result("peek", _large_explore_result("skill-a", "skill-b")),
        _assistant_tool_call("compose-ready", "symphony_compose_graph"),
        _tool_result("compose-ready", _ready_plan("skill-a")),
        _assistant_tool_call("other", "read_file"),
        _tool_result("other", {"content": "keep this result"}),
        _assistant_tool_call("explore-later", "skill_branch_explore"),
        _tool_result("explore-later", _large_explore_result("skill-b")),
        _assistant_tool_call("compose-needs-input", "symphony_compose_graph"),
        _tool_result(
            "compose-needs-input",
            {
                "success": True,
                "planned_graph": {
                    "graph": {
                        "type": "planned_graph",
                        "metadata": {
                            "status": "needs_input",
                            "missing_inputs": ["data"],
                        },
                        "nodes": {},
                        "edges": [],
                    }
                },
            },
        ),
    ]

    event, projected = await _project(messages)

    assert event is not None
    assert event.messages_to_modify == [2, 4]
    assert projected.context_messages[2].content != messages[2].content
    assert projected.context_messages[4].content != messages[4].content
    assert projected.context_messages[8].content == messages[8].content
    assert projected.context_messages[10].content == messages[10].content


@pytest.mark.asyncio
async def test_keeps_consumed_retrieval_compacted_in_later_user_rounds() -> None:
    messages = [
        UserMessage(content="first task"),
        _assistant_tool_call("explore", "skill_branch_explore"),
        _tool_result("explore", _large_explore_result("skill-a")),
        _assistant_tool_call("compose", "symphony_compose_graph"),
        _tool_result("compose", _ready_plan("skill-a")),
        UserMessage(content="follow-up"),
        AssistantMessage(content="continue"),
    ]

    event, projected = await _project(messages)

    assert event is not None
    assert event.messages_to_modify == [2]
    assert projected.context_messages[2].content != messages[2].content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "compose_payload",
    [
        {"success": False},
        {
            "success": True,
            "planned_graph": {
                "graph": {
                    "type": "planned_graph",
                    "metadata": {"status": "needs_input", "missing_inputs": ["x"]},
                    "nodes": {},
                    "edges": [],
                }
            },
        },
        {
            "success": True,
            "planned_graph": {
                "graph": {
                    "type": "planned_graph",
                    "metadata": {"status": "ready"},
                    "nodes": {},
                    "edges": [],
                }
            },
        },
        {
            "success": True,
            "planned_graph": {
                "graph": {
                    "type": "planned_graph",
                    "metadata": {"status": "ready"},
                    "nodes": {"tool-a": {"metadata": {"type": "tool"}}},
                    "edges": [],
                }
            },
        },
        {
            "success": True,
            "planned_graph": {
                "graph": {
                    "type": "planned_graph",
                    "metadata": {"status": "ready"},
                    "nodes": {"skill-a": {"metadata": {"type": "skill"}}},
                    "edges": [{"source": "skill-a", "target": "missing"}],
                }
            },
        },
        {"success": True, "planned_graph": "malformed"},
    ],
)
async def test_keeps_retrieval_for_non_executable_or_malformed_plans(
    compose_payload: dict,
) -> None:
    explore = _tool_result("explore", _large_explore_result("skill-a"))
    messages = [
        UserMessage(content="compose a workflow"),
        _assistant_tool_call("explore", "skill_branch_explore"),
        explore,
        _assistant_tool_call("compose", "symphony_compose_graph"),
        _tool_result("compose", compose_payload),
    ]

    event, projected = await _project(messages)

    assert event is None
    assert projected.context_messages[2].content == explore.content


@pytest.mark.asyncio
async def test_does_not_compact_retrieval_from_previous_round_without_compose() -> None:
    explore = _tool_result("explore", _large_explore_result("skill-a"))
    messages = [
        UserMessage(content="browse only"),
        _assistant_tool_call("explore", "skill_branch_explore"),
        explore,
        UserMessage(content="new task"),
        AssistantMessage(content="answer"),
    ]

    event, projected = await _project(messages)

    assert event is None
    assert projected.context_messages[2].content == explore.content


@pytest.mark.asyncio
async def test_does_not_replace_result_when_summary_is_not_shorter() -> None:
    compact_candidate = {"success": True, "skill_tree": {"candidates": []}}
    messages = [
        UserMessage(content="compose a workflow"),
        _assistant_tool_call("explore", "skill_branch_explore"),
        _tool_result("explore", compact_candidate),
        _assistant_tool_call("compose", "symphony_compose_graph"),
        _tool_result("compose", _ready_plan("skill-a")),
    ]

    event, projected = await _project(messages)

    assert event is None
    assert projected.context_messages[2].content == messages[2].content
