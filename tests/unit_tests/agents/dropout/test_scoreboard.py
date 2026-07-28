# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ContributionScoreboard."""

from __future__ import annotations

from jiuwenswarm.agents.dropout.scoreboard import ContributionScoreboard
from jiuwenswarm.agents.dropout.types import AuditJudgement


def test_prune_messages_filters_pruned_entries():
    board = ContributionScoreboard(prune_enabled=True)
    board.update(
        message_id="m1",
        content="good",
        source="a",
        judgements=[AuditJudgement(metric="x", verdict="correct")],
        is_pruned=False,
    )
    board.update(
        message_id="m2",
        content="bad",
        source="b",
        judgements=[AuditJudgement(metric="x", verdict="flawed")],
        is_pruned=True,
    )

    messages = [
        {"id": "m1", "source": "a", "content": "good"},
        {"id": "m2", "source": "b", "content": "bad"},
        {"id": "user-1", "source": "user", "content": "question"},
    ]
    kept = board.prune_messages(messages)
    assert [m["id"] for m in kept] == ["m1", "user-1"]


def test_prune_disabled_keeps_all():
    board = ContributionScoreboard(prune_enabled=False)
    board.update(
        message_id="m2",
        content="bad",
        source="b",
        judgements=[],
        is_pruned=True,
    )
    messages = [{"id": "m2", "source": "b"}]
    assert board.prune_messages(messages) == messages
    assert board.is_pruned("m2") is False


def test_get_messages_above_threshold_excludes_pruned():
    board = ContributionScoreboard()
    board.update(message_id="ok", content="c", source="a", judgements=[], is_pruned=False)
    board.update(message_id="bad", content="c", source="b", judgements=[], is_pruned=True)
    above = board.get_messages_above_threshold()
    assert [e.message_id for e in above] == ["ok"]
