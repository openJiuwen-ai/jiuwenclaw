# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TaskTool usage patch tags subagent calls for by_agent."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.server.runtime.usage import task_tool_usage_patch


class _FakeSession:
    def __init__(self) -> None:
        self.written: list = []

    def get_session_id(self) -> str:
        return "parent-session"

    async def write_stream(self, data) -> None:
        self.written.append(data)


@pytest.mark.asyncio
async def test_emit_usage_tags_subagent_type() -> None:
    session = _FakeSession()
    await task_tool_usage_patch._emit_usage_to_parent(
        session,
        subagent_type="research_agent",
        events=[
            {
                "model_name": "cheap-model",
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
            }
        ],
    )
    assert len(session.written) == 1
    chunk = session.written[0]
    assert chunk.type == "llm_usage"
    meta = chunk.payload["usage_metadata"]
    assert meta["subagent_type"] == "research_agent"
    assert meta["agent_id"] == "research_agent"
    assert meta["input_tokens"] == 100


def test_usage_meta_dict_from_object() -> None:
    usage = SimpleNamespace(
        model_name="m",
        input_tokens=1,
        output_tokens=2,
        total_tokens=3,
        cache_tokens=0,
    )
    meta = task_tool_usage_patch._usage_meta_dict(usage)
    assert meta == {
        "model_name": "m",
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
        "cache_tokens": 0,
    }
