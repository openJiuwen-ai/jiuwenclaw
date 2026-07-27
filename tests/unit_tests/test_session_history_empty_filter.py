import time

from jiuwenswarm.server.runtime.session import session_history


def _wait_history(session_id: str, *, min_count: int = 1):
    deadline = time.time() + 5
    while time.time() < deadline:
        data = session_history.load_history_records(session_id)
        if len(data) >= min_count:
            return data
        time.sleep(0.05)
    return session_history.load_history_records(session_id)


def test_append_history_skips_empty_chat_final_and_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(session_history, "get_agent_sessions_dir", lambda: tmp_path)

    session_history.append_history_record(
        session_id="s-empty",
        request_id="r1",
        channel_id="web",
        role="assistant",
        event_type="chat.final",
        content="   ",
        timestamp=1.0,
    )
    session_history.append_history_record(
        session_id="s-empty",
        request_id="r2",
        channel_id="web",
        role="assistant",
        event_type="chat.final",
        content="HEARTBEAT_OK",
        timestamp=2.0,
    )
    session_history.append_history_record(
        session_id="heartbeat_abc",
        request_id="r3",
        channel_id="heartbeat",
        role="user",
        content="heartbeat prompt",
        timestamp=3.0,
    )
    session_history.append_history_record(
        session_id="s-empty",
        request_id="r4",
        channel_id="web",
        role="assistant",
        event_type="chat.file",
        content="",
        timestamp=4.0,
        extra={"files": [{"path": "/tmp/a.md", "name": "a.md"}]},
    )
    session_history.append_history_record(
        session_id="s-empty",
        request_id="r5",
        channel_id="web",
        role="assistant",
        event_type="chat.final",
        content="hello",
        timestamp=5.0,
    )

    data = _wait_history("s-empty", min_count=2)
    assert [item.get("event_type") for item in data] == ["chat.file", "chat.final"]
    assert data[1]["content"] == "hello"
    assert session_history.load_history_records("heartbeat_abc") == []
