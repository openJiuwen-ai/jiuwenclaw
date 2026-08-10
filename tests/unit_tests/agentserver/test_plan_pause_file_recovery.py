# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for plan_pause disk recovery (survives checkpoint overwrite)."""

from __future__ import annotations

from pathlib import Path

from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import (
    clear_plan_pause_file,
    read_plan_pause_from_file,
    write_plan_pause_to_file,
)


def test_plan_pause_file_roundtrip(tmp_path: Path) -> None:
    session_id = "officeclaw_sess_1"
    snapshot = {"todos": [{"id": "t1", "content": "old task", "status": "pending"}]}

    write_plan_pause_to_file(tmp_path, session_id, snapshot)
    paused, loaded = read_plan_pause_from_file(tmp_path, session_id)

    assert paused is True
    assert loaded == snapshot


def test_plan_pause_file_missing(tmp_path: Path) -> None:
    paused, snapshot = read_plan_pause_from_file(tmp_path, "missing")
    assert paused is False
    assert snapshot is None


def test_plan_pause_file_clear(tmp_path: Path) -> None:
    session_id = "officeclaw_sess_2"
    write_plan_pause_to_file(tmp_path, session_id, {"todos": []})
    clear_plan_pause_file(tmp_path, session_id)

    paused, snapshot = read_plan_pause_from_file(tmp_path, session_id)
    assert paused is False
    assert snapshot is None
