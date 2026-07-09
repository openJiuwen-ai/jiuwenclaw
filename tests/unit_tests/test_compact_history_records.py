import json
import time

from jiuwenavatar.server.runtime.session import session_history


def _read_history_file(path):
    deadline = time.time() + 5
    while time.time() < deadline:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if len(data) >= 2:
                return data
        time.sleep(0.05)
    raise AssertionError(f"history file was not written: {path}")


def test_append_compact_history_records_writes_boundary_and_transcript_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(session_history, "get_agent_sessions_dir", lambda: tmp_path)

    session_history.append_compact_history_records(
        session_id="s1",
        request_id="r1",
        channel_id="tui",
        summary="Important compact summary",
        timestamp=123.0,
        trigger="auto",
        stats={"processor": "RoundLevelCompressor"},
        mode="agent.plan",
    )

    data = _read_history_file(tmp_path / "s1" / "history.json")

    assert [item["event_type"] for item in data] == [
        "context.compact_boundary",
        "context.compact_summary",
    ]
    assert data[0]["content"] == "Conversation compacted"
    assert data[0]["compact_metadata"]["trigger"] == "auto"
    assert data[0]["compact_metadata"]["processor"] == "RoundLevelCompressor"
    assert data[1]["content"] == "Important compact summary"
    assert data[1]["is_compact_summary"] is True
    assert data[1]["transcript_only"] is True
