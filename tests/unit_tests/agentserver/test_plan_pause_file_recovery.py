# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for plan_pause / artifacts fields in shared recovery.json."""

from __future__ import annotations

import json
from pathlib import Path

from jiuwenclaw.agentserver.deep_agent.plan_pause_helpers import (
    clear_interrupt_artifacts_file,
    clear_plan_pause_file,
    clear_recovery_file,
    read_interrupt_artifacts_from_file,
    read_plan_pause_from_file,
    write_interrupt_artifacts_to_file,
    write_plan_pause_to_file,
)


def _recovery_path(workspace: Path, session_id: str) -> Path:
    return workspace / "context" / session_id / "recovery" / "recovery.json"


def test_plan_pause_file_roundtrip(tmp_path: Path) -> None:
    session_id = "officeclaw_sess_1"
    snapshot = {"todos": [{"id": "t1", "content": "old task", "status": "pending"}]}

    write_plan_pause_to_file(tmp_path, session_id, snapshot)
    paused, loaded = read_plan_pause_from_file(tmp_path, session_id)

    assert paused is True
    assert loaded == snapshot
    assert _recovery_path(tmp_path, session_id).exists()


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
    assert not _recovery_path(tmp_path, session_id).exists()


def test_plan_pause_and_artifacts_coexist_in_recovery_json(tmp_path: Path) -> None:
    session_id = "officeclaw_sess_3"
    snapshot = {"todos": [{"id": "t1", "status": "pending"}]}

    write_plan_pause_to_file(tmp_path, session_id, snapshot)
    write_interrupt_artifacts_to_file(tmp_path, session_id, "- write_file a.docx (产物已生成)")

    paused, loaded = read_plan_pause_from_file(tmp_path, session_id)
    summary = read_interrupt_artifacts_from_file(tmp_path, session_id)

    assert paused is True
    assert loaded == snapshot
    assert summary and "a.docx" in summary

    data = json.loads(_recovery_path(tmp_path, session_id).read_text(encoding="utf-8"))
    assert data["plan_paused"] is True
    assert data["summary"].startswith("- write_file")

    # Field-level clear of pause must keep artifacts summary.
    clear_plan_pause_file(tmp_path, session_id)
    assert read_plan_pause_from_file(tmp_path, session_id) == (False, None)
    assert "a.docx" in (read_interrupt_artifacts_from_file(tmp_path, session_id) or "")

    # Field-level clear of artifacts removes the file when no other payload remains.
    clear_interrupt_artifacts_file(tmp_path, session_id)
    assert read_interrupt_artifacts_from_file(tmp_path, session_id) is None
    assert not _recovery_path(tmp_path, session_id).exists()

    # After both are written again, consuming recovery deletes the whole file.
    write_plan_pause_to_file(tmp_path, session_id, snapshot)
    write_interrupt_artifacts_to_file(tmp_path, session_id, "- write_file a.docx (产物已生成)")
    clear_recovery_file(tmp_path, session_id)
    assert read_plan_pause_from_file(tmp_path, session_id) == (False, None)
    assert read_interrupt_artifacts_from_file(tmp_path, session_id) is None
    assert not _recovery_path(tmp_path, session_id).exists()
