"""Regression coverage for turn diffs consumed by Code-mode UI."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from jiuwenclaw.agentserver.diff_service import DiffService


def test_turn_diff_keeps_file_operations_visible(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-1"
    session_dir.mkdir()
    (session_dir / "history.json").write_text(
        json.dumps(
            [
                {
                    "role": "user",
                    "content": "update the file",
                    "timestamp": 100.0,
                },
                {
                    "role": "assistant",
                    "event_type": "chat.final",
                    "timestamp": 101.0,
                },
            ]
        ),
        encoding="utf-8",
    )
    service = DiffService()
    service._read_agent_history = MagicMock(
        return_value={
            "main.py": [
                {
                    "timestamp": "1970-01-01T00:01:40+00:00",
                    "action": "write",
                    "old_content": "old\n",
                    "new_content": "new\n",
                }
            ]
        }
    )

    turns = service.get_turn_diffs("session-1", sessions_root=tmp_path)

    assert turns[0]["files"]["main.py"]["linesAdded"] == 1
    assert turns[0]["files"]["main.py"]["linesRemoved"] == 1
    assert turns[0]["stats"]["filesChanged"] == 1
