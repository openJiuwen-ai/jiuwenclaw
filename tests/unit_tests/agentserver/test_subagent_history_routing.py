# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for parent-owned subagent history persistence."""

from __future__ import annotations

from jiuwenswarm.server.runtime.agent_adapter import interface_deep
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
)


def test_subagent_history_writes_use_dedicated_child_bucket(monkeypatch) -> None:
    persisted = []
    monkeypatch.setattr(
        interface_deep,
        "append_history_record",
        lambda **kwargs: persisted.append(kwargs),
    )
    projection = {
        "parent_session_id": "parent-session",
        "subagent_id": "subagent-1",
        "task_id": "task-1",
        "seq": 1,
        "role": "assistant",
        "content": "result",
        "summary": "working",
        "event_type": "chat.final",
    }

    JiuWenSwarmDeepAdapter._persist_subagent_transcript_message(projection)
    JiuWenSwarmDeepAdapter._persist_subagent_activity(projection)
    JiuWenSwarmDeepAdapter._persist_subagent_roster_history(
        projection,
        {"description": "worker"},
    )

    assert len(persisted) == 3
    assert all(item["session_id"] == "parent-session" for item in persisted)
    assert all(item["subagent_id"] == "subagent-1" for item in persisted)
    assert all(item["mode"] == "subagent" for item in persisted)
