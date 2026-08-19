# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for wire-layer tool pairing repair (``_sanitize_wire_tool_pairing``).

Covers the ``ModelArts.81001`` repro: ``assistant(tool_calls) -> user -> tool``
gets repaired to ``assistant(tool_calls) -> tool -> user`` without deleting
any message.
"""
from __future__ import annotations

from jiuwenclaw.jiuwen_core_patch import _sanitize_wire_tool_pairing


def _assistant(tool_call_id: str, tool_name: str = "todo_list") -> dict:
    return {
        "role": "assistant",
        "content": "let me check todos",
        "tool_calls": [
            {
                "id": tool_call_id,
                "type": "function",
                "function": {"name": tool_name, "arguments": "{}"},
            }
        ],
    }


def _assistant_multi(*ids: str) -> dict:
    """Assistant declaring multiple tool_calls (one per id)."""
    return {
        "role": "assistant",
        "content": "running several tools",
        "tool_calls": [
            {
                "id": tid,
                "type": "function",
                "function": {"name": f"tool_{tid}", "arguments": "{}"},
            }
            for tid in ids
        ],
    }


def _tool(tool_call_id: str, content: str = "Todo List") -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


def _user(content: str) -> dict:
    return {"role": "user", "content": content}


def _system(content: str) -> dict:
    return {"role": "system", "content": content}


def _roles(messages: list[dict]) -> list[str]:
    return [m["role"] for m in messages]


def test_adsorbs_tool_back_after_injected_user_messages():
    """81001 repro: assistant(tool_calls) -> user -> user -> tool."""
    params = {
        "messages": [
            _assistant("call_1"),
            _user("[FULL_COMPACT_STATE]\n[PLAN_MODE]\n..."),
            _user("[FULL_COMPACT_STATE]\n[TODOS ...]"),
            _tool("call_1"),
        ]
    }
    _sanitize_wire_tool_pairing(params)

    repaired = params["messages"]
    # tool now immediately follows the assistant; users shifted behind
    assert _roles(repaired) == ["assistant", "tool", "user", "user"]
    assert repaired[1]["tool_call_id"] == "call_1"
    # no message dropped
    assert len(repaired) == 4


def test_no_repair_when_already_adjacent():
    params = {
        "messages": [
            _assistant("call_1"),
            _tool("call_1"),
            _user("next question"),
        ]
    }
    _sanitize_wire_tool_pairing(params)
    assert _roles(params["messages"]) == ["assistant", "tool", "user"]


def test_no_repair_for_legal_consecutive_tools_resolving_to_assistant():
    """assistant(c1,c2) -> tool(c1) -> tool(c2) is legal; must NOT trigger repair.

    Guards the quick-scan false-positive fixed by the pending-id set.
    """
    params = {
        "messages": [
            _assistant_multi("call_1", "call_2"),
            _tool("call_1"),
            _tool("call_2"),
            _user("ok"),
        ]
    }
    _sanitize_wire_tool_pairing(params)
    assert _roles(params["messages"]) == ["assistant", "tool", "tool", "user"]


def test_stranded_valid_tool_is_recovered_not_downgraded():
    """A valid tool result stranded downstream of its assistant's (truncated)
    adsorb segment must be spliced back behind its assistant, not downgraded.

    Reproduces the cross-assistant misdowngrade: a true orphan elsewhere
    triggers repair; assistant(call_1)'s segment is broken by assistant(call_2)
    before tool(call_1) appears, so tool(call_1) reaches the orphan branch.
    Its id is valid (declared by assistant(call_1)) → recover, don't downgrade.

    Non-compact sources (history replay, subagent context inheritance) can
    deliver such misordered payloads, so the stranded_recover path is kept.
    """
    params = {
        "messages": [
            _tool("ghost", content="real orphan"),  # true orphan → triggers repair
            _assistant("call_1"),
            _assistant("call_2"),  # breaks call_1's adsorb segment
            _tool("call_2", content="result-c2"),
            _tool("call_1", content="result-c1"),  # stranded, but valid id
        ]
    }
    _sanitize_wire_tool_pairing(params)
    repaired = params["messages"]

    # ghost (true orphan) downgraded; call_1 (valid) recovered as a tool
    assert repaired[0]["role"] == "user"
    assert "real orphan" in repaired[0]["content"]
    # call_1's tool result sits right behind its assistant, still a tool
    for k, m in enumerate(repaired):
        if (m["role"] == "assistant"
                and isinstance(m.get("tool_calls"), list)
                and any(tc.get("id") == "call_1" for tc in m["tool_calls"] if isinstance(tc, dict))):
            assert repaired[k + 1]["role"] == "tool"
            assert repaired[k + 1]["tool_call_id"] == "call_1"
            assert repaired[k + 1]["content"] == "result-c1"
            break
    else:
        raise AssertionError("call_1 assistant not found")
    # nothing deleted: 5 in, 5 out
    assert len(repaired) == 5


def test_system_between_assistant_and_tool_is_deferred_not_breaking():
    """system wedged between assistant.tool_calls and its tool result is deferred
    behind the tool (never dropped), and the tool is NOT downgraded as an orphan.

    Guards the regression where treating system as a hard-boundary ``break``
    stranded the later resolvable tool result into orphan-downgrade.
    """
    params = {
        "messages": [
            _assistant("call_1"),
            _system("a system prompt injected mid-sequence"),
            _tool("call_1", content="the real result"),
        ]
    }
    _sanitize_wire_tool_pairing(params)
    repaired = params["messages"]
    # tool adsorbed right behind the assistant; system shifted behind the tool
    assert _roles(repaired) == ["assistant", "tool", "system"]
    assert repaired[1]["tool_call_id"] == "call_1"
    assert repaired[1]["content"] == "the real result"
    # system preserved as-is, not downgraded or dropped
    assert repaired[2]["role"] == "system"
    assert repaired[2]["content"] == "a system prompt injected mid-sequence"
    assert len(repaired) == 3


def test_downgrades_true_orphan_tool_to_user_without_deleting():
    # tool whose tool_call_id matches no assistant.tool_calls
    params = {
        "messages": [
            _user("hello"),
            _tool("call_orphan", content="orphan result"),
        ]
    }
    _sanitize_wire_tool_pairing(params)

    repaired = params["messages"]
    assert repaired[1]["role"] == "user"
    assert "orphan result" in repaired[1]["content"]
    assert "call_orphan" in repaired[1]["content"]
    assert "tool_call_id" not in repaired[1]
    assert len(repaired) == 2  # nothing deleted


def test_orphan_downgrade_does_not_mutate_original_message_object():
    """The original tool dict in the source list must be left untouched (#4)."""
    orphan = _tool("call_orphan", content="orphan result")
    params = {"messages": [_user("hello"), orphan]}
    _sanitize_wire_tool_pairing(params)

    # original object is unchanged — caller's snapshot stays stable
    assert orphan["role"] == "tool"
    assert orphan["content"] == "orphan result"
    assert orphan["tool_call_id"] == "call_orphan"


def test_orphan_downgrade_handles_non_str_content():
    """Tool content may be a structured list; downgrade must not crash or lose it."""
    structured = [{"type": "text", "text": "part-A"}, {"type": "text", "text": "part-B"}]
    params = {
        "messages": [
            _user("hello"),
            {"role": "tool", "tool_call_id": "call_x", "content": structured},
        ]
    }
    _sanitize_wire_tool_pairing(params)

    repaired = params["messages"]
    assert repaired[1]["role"] == "user"
    assert isinstance(repaired[1]["content"], str)
    assert "part-A" in repaired[1]["content"]
    assert "part-B" in repaired[1]["content"]
    assert "call_x" in repaired[1]["content"]


def test_adsorbs_multiple_rounds_independently():
    params = {
        "messages": [
            _assistant("call_1"),
            _user("injected-1"),
            _tool("call_1"),
            _assistant("call_2"),
            _user("injected-2"),
            _tool("call_2"),
        ]
    }
    _sanitize_wire_tool_pairing(params)
    assert _roles(params["messages"]) == [
        "assistant",
        "tool",
        "user",
        "assistant",
        "tool",
        "user",
    ]


def test_adsorbs_multiple_tool_results_for_one_assistant_split_by_users():
    """assistant(c1,c2) -> user -> tool(c1) -> user -> tool(c2): both adsorbed forward."""
    params = {
        "messages": [
            _assistant_multi("call_1", "call_2"),
            _user("injected-A"),
            _tool("call_1"),
            _user("injected-B"),
            _tool("call_2"),
        ]
    }
    _sanitize_wire_tool_pairing(params)
    repaired = params["messages"]
    # both tools pulled right behind the assistant, users shifted behind
    assert _roles(repaired) == ["assistant", "tool", "tool", "user", "user"]
    assert repaired[1]["tool_call_id"] == "call_1"
    assert repaired[2]["tool_call_id"] == "call_2"


def test_empty_or_non_list_messages_is_noop():
    _sanitize_wire_tool_pairing({"messages": []})
    _sanitize_wire_tool_pairing({"messages": None})
    _sanitize_wire_tool_pairing({})  # no messages key


def test_no_repair_for_consecutive_tools_resolving_to_same_assistant():
    """assistant(c1,c2) -> tool(c1) -> tool(c2) is legal; must NOT trigger repair.

    Guards the quick-scan false-positive where a naive prev-flag would mark the
    second consecutive tool as non-adjacent. The pending-id set + segment-tool
    flag must accept consecutive tools resolving to the most recent assistant.
    """
    params = {
        "messages": [
            _assistant_multi("call_1", "call_2"),
            _tool("call_1"),
            _tool("call_2"),
            _user("ok"),
        ]
    }
    _sanitize_wire_tool_pairing(params)
    assert _roles(params["messages"]) == ["assistant", "tool", "tool", "user"]


def test_adsorbs_multiple_tool_calls_for_single_assistant():
    """assistant(c1,c2) -> user -> tool(c1) -> tool(c2): both adsorbed, user deferred.

    The common multi-tool-call compact case: one injected user splits a single
    assistant's two tool results. Both must be adsorbed behind the assistant and
    the user shifted behind them; nothing deleted.
    """
    params = {
        "messages": [
            _assistant_multi("call_1", "call_2"),
            _user("[FULL_COMPACT_STATE] ..."),
            _tool("call_1"),
            _tool("call_2"),
        ]
    }
    _sanitize_wire_tool_pairing(params)
    repaired = params["messages"]
    assert _roles(repaired) == ["assistant", "tool", "tool", "user"]
    assert repaired[1]["tool_call_id"] == "call_1"
    assert repaired[2]["tool_call_id"] == "call_2"
    assert len(repaired) == 4


def test_stranded_valid_tool_across_assistant_boundary_not_downgraded():
    """A valid tool result stranded across an assistant boundary must survive.

    Sequence: assistant(c1) -> user -> assistant(c2) -> tool(c2) -> tool(c1).
    call_1's forward window is broken by assistant(c2) before tool(c1) arrives,
    so tool(c1) reaches the orphan branch. Its id is declared (by
    assistant(c1)) so it must be RECOVERED, not downgraded to user.

    Guards the cross-assistant misdowngrade (MF-002): a valid id reaching the
    orphan branch because its segment was truncated by a later assistant.
    """
    params = {
        "messages": [
            _assistant("call_1"),
            _user("[FULL_COMPACT_STATE] ..."),
            _assistant("call_2"),
            _tool("call_2", content="result-c2"),
            _tool("call_1", content="result-c1"),
        ]
    }
    _sanitize_wire_tool_pairing(params)
    repaired = params["messages"]

    # tool(c1) stays a tool (recovered), never downgraded to user
    c1_tool = next(
        m for m in repaired
        if m.get("role") == "tool" and m.get("tool_call_id") == "call_1"
    )
    assert c1_tool["content"] == "result-c1"
    # no message downgraded to user carrying the c1 result
    assert not any(
        m.get("role") == "user" and "result-c1" in str(m.get("content", ""))
        for m in repaired
    )
    # nothing deleted
    assert len(repaired) == 5
