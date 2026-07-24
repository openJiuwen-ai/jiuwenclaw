# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for stale todo cleanup gating."""

from __future__ import annotations

from types import SimpleNamespace

from jiuwenswarm.server.runtime.agent_adapter.stale_todo_cleanup_helpers import (
    is_resume_user_query,
    should_cancel_stale_active_todos,
)


def test_resume_phrases_are_detected() -> None:
    assert is_resume_user_query("继续")
    assert is_resume_user_query("接着做")
    assert is_resume_user_query("continue")
    assert not is_resume_user_query("帮我写一份报告")


def test_should_cancel_skips_supplement_and_resume() -> None:
    request = SimpleNamespace(session_id="s1", request_id="r1", params={})
    assert should_cancel_stale_active_todos(
        request, {"query": "新任务", "is_supplement": True}
    ) is False
    assert should_cancel_stale_active_todos(request, {"query": "继续"}) is False
    assert should_cancel_stale_active_todos(request, {"query": "新任务"}) is True


def test_should_cancel_skips_heartbeat_session() -> None:
    request = SimpleNamespace(session_id="heartbeat-1", request_id="r1", params={})
    assert should_cancel_stale_active_todos(request, {"query": "x"}) is False
