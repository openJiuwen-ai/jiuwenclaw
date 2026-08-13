# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Only team-wide input gets the user-message envelope.

Member-addressed messages already travel inside the team's own
``<team-inbound from=... type=...>`` envelope, so they must reach the member
verbatim — nesting the user-input envelope inside that one puts two
contradicting headers on the same message.
"""

from __future__ import annotations

import json

import pytest

from jiuwenswarm.server.runtime.agent_adapter.team_helpers import (
    _deliverable,
    _interact_payloads,
    _is_member_addressed,
    _start_team_stream_round,
    _split_multi_member_addressed,
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
    "text",
    [
        "$human-member-1 @member-1 hello",
        "@member-1 看一下",
        "@all 停一下",
        "$human-reporter 我来说两句",
    ],
)
def test_member_addressed_messages_are_delivered_verbatim(text: str):
    assert _is_member_addressed(text) is True
    assert _deliverable(_turn(), text) == text


@pytest.mark.parametrize(
    "text",
    [
        "hello 团队",
        "# 大家注意",
        # A bare @name without the trailing space is god-view per openjiuwen's
        # own grammar, so it stays team-wide input here too.
        "@reviewer",
    ],
)
def test_team_wide_input_gets_the_envelope(text: str):
    assert _is_member_addressed(text) is False

    envelope = _envelope(_deliverable(_turn(), text))

    assert envelope["content"] == text
    assert "需求.md" in envelope["files_updated_by_user"]


def test_direct_message_keeps_its_own_wording():
    """Regression: the inner envelope contradicted the team-inbound header.

    A member received ``<team-inbound from="human-member-1">`` wrapping a
    ``{"source": "web", "type": "user input"}`` payload whose timestamp and
    files fields meant nothing on that channel.
    """
    delivered = _deliverable(_turn(), "$human-member-1 @member-1 hello")

    assert delivered == "$human-member-1 @member-1 hello"
    assert "你收到一条消息" not in delivered
    assert "files_updated_by_user" not in delivered


def test_multi_member_shorthand_splits_into_direct_messages():
    text = "$human-member @member-1 你来查询杭州今日天气 @member-2 你来查询上海今日天气"

    assert _split_multi_member_addressed(text) == [
        "$human-member @member-1 你来查询杭州今日天气",
        "$human-member @member-2 你来查询上海今日天气",
    ]


@pytest.mark.asyncio
async def test_multi_member_shorthand_interacts_once_per_target():
    class TeamManager:
        def __init__(self):
            self.calls = []

        async def interact(self, session_id: str, payload: str):
            self.calls.append((session_id, payload))
            return True, None

    manager = TeamManager()

    success, reason = await _interact_payloads(
        manager,
        "sess-1",
        "$human-member @member-1 你来查询杭州今日天气 @member-2 你来查询上海今日天气",
    )

    assert success is True
    assert reason is None
    assert manager.calls == [
        ("sess-1", "$human-member @member-1 你来查询杭州今日天气"),
        ("sess-1", "$human-member @member-2 你来查询上海今日天气"),
    ]


@pytest.mark.asyncio
async def test_first_request_multi_member_shorthand_starts_with_first_target(monkeypatch):
    captured = {}

    class TeamManager:
        async def prepare_runtime_activation(self, session_id: str, team_name: str):
            captured["prepared"] = (session_id, team_name)

        def add_waiter(self, session_id: str, request_id: str, queue):
            captured["waiter"] = (session_id, request_id)

        def register_stream_task(self, session_id: str, task):
            captured["stream_task"] = (session_id, task)

    async def fake_consume(channel_id, session_id, team_spec, initial_query, *, round_id, envs=None):
        captured["stream"] = {
            "channel_id": channel_id,
            "session_id": session_id,
            "initial_query": initial_query,
            "round_id": round_id,
            "envs": envs,
        }

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.sync_team_observability",
        lambda: None,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.team_helpers.increment_session_round_count",
        lambda _session_id: 7,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.team_helpers._consume_stream_with_query",
        fake_consume,
    )

    await _start_team_stream_round(
        channel_id="web",
        session_id="sess-1",
        request_id="req-1",
        team_manager=TeamManager(),
        team_name="team-1",
        team_spec=object(),
        query="$human-member @member-1 你来查询杭州今日天气 @member-2 你来查询上海今日天气",
    )

    task = captured["stream_task"][1]
    await task
    assert captured["stream"]["initial_query"] == "$human-member @member-1 你来查询杭州今日天气"
    assert captured["stream"]["envs"]["pending_interacts"] == [
        "$human-member @member-2 你来查询上海今日天气",
    ]


def test_non_text_payload_passes_through():
    marker = object()

    assert _deliverable(_turn(), marker) is marker


def test_empty_text_is_treated_as_team_wide():
    assert _is_member_addressed("") is False
