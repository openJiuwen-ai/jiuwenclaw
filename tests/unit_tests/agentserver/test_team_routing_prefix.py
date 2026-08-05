# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Routing tokens stay outside the rendered envelope so @member still routes."""

from __future__ import annotations

import json

import pytest
from openjiuwen.agent_teams.interaction.router import parse_interact_str

from jiuwenswarm.server.runtime.agent_adapter.team_helpers import (
    _deliverable,
    _split_team_routing_prefix,
)
from jiuwenswarm.server.runtime.agent_adapter.user_turn import UserTurn


def _turn() -> UserTurn:
    return UserTurn(
        text="",
        channel="web",
        language="zh",
        files={"uploaded_documents": [{"filename": "需求.md", "path": "/uploads/需求.md"}]},
    )


def _envelope(rendered: str) -> dict:
    return json.loads(rendered[rendered.index("{"):])


@pytest.mark.parametrize(
    ("text", "prefix", "body"),
    [
        ("@reviewer 看一下", "@reviewer ", "看一下"),
        ("$human-reporter @coder 改一下", "$human-reporter @coder ", "改一下"),
        ("# 大家注意", "# ", "大家注意"),
        ("普通消息", "", "普通消息"),
    ],
)
def test_split_routing_prefix(text: str, prefix: str, body: str):
    assert _split_team_routing_prefix(text) == (prefix, body)


def test_mention_still_routes_to_the_member():
    """Regression: folding @member into the envelope made every message god-view.

    The leader then had to read the text and re-dispatch by hand instead of the
    runtime delivering straight to the named member.
    """
    delivered = _deliverable(_turn(), "@reviewer 看一下这个文档")

    payloads = parse_interact_str(delivered)

    assert len(payloads) == 1
    assert type(payloads[0]).__name__ == "OperatorMessage"
    assert payloads[0].target == "reviewer"


def test_human_agent_prefix_keeps_sender_and_target():
    delivered = _deliverable(_turn(), "$human-reporter @coder 改一下")

    payload = parse_interact_str(delivered)[0]

    assert type(payload).__name__ == "HumanAgentMessage"
    assert payload.sender == "human-reporter"
    assert payload.target == "coder"


def test_broadcast_prefix_is_preserved():
    delivered = _deliverable(_turn(), "@all 停一下")

    payload = parse_interact_str(delivered)[0]

    assert type(payload).__name__ == "OperatorMessage"
    assert payload.target is None


def test_routed_body_is_the_full_envelope():
    delivered = _deliverable(_turn(), "@reviewer 看一下这个文档")

    envelope = _envelope(parse_interact_str(delivered)[0].body)

    assert envelope["content"] == "看一下这个文档"
    assert "需求.md" in envelope["files_updated_by_user"]


def test_plain_message_renders_without_a_prefix():
    delivered = _deliverable(_turn(), "总结一下")

    assert delivered.startswith("你收到一条消息：")
    assert _envelope(delivered)["content"] == "总结一下"


def test_non_text_payload_passes_through():
    marker = object()

    assert _deliverable(_turn(), marker) is marker
