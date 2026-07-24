# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for per-session tool id qualification."""

from __future__ import annotations

from jiuwenswarm.server.runtime.agent_adapter.tool_qualify import qualify_tool_id


def test_qualify_tool_id_appends_agent_suffix() -> None:
    assert qualify_tool_id("send_file_to_user", "jiuwenswarm_s1") == (
        "send_file_to_user_jiuwenswarm_s1"
    )


def test_qualify_tool_id_is_idempotent() -> None:
    first = qualify_tool_id("paid_search", "jiuwenswarm_abc")
    assert qualify_tool_id(first, "jiuwenswarm_abc") == first


def test_infer_multimodal_env_removals_on_full_snapshot_omission() -> None:
    from jiuwenswarm.agents.harness.common.tools.multimodal_config import (
        infer_multimodal_env_removals,
    )

    previous = {"VISION_API_KEY": "old"}
    new_env = {"API_KEY": "k", "MODEL_NAME": "m"}
    removals = infer_multimodal_env_removals(previous, new_env, active_env=previous)
    assert "VISION_API_KEY" in removals
    assert "VISION_API_BASE" in removals
