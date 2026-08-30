import json

from jiuwenswarm.server.runtime.session import session_history


def test_read_history_paths_do_not_create_missing_session_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(session_history, "get_agent_sessions_dir", lambda: tmp_path)

    session_id = "sess_missing"
    session_dir = tmp_path / session_id

    read_path = session_history.get_read_history_path(session_id)

    assert read_path == session_dir / "history.jsonl"
    assert not session_dir.exists()
    assert not session_history.history_exists(session_id)
    assert session_history.load_history_records(session_id) == []
    assert not session_dir.exists()


def test_write_history_path_still_creates_session_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(session_history, "get_agent_sessions_dir", lambda: tmp_path)

    session_id = "sess_new"
    session_dir = tmp_path / session_id

    write_path = session_history.get_write_history_path(session_id)

    assert write_path == session_dir / "history.jsonl"
    assert session_dir.is_dir()


def test_load_history_records_dedups_same_id_last_wins(tmp_path, monkeypatch):
    """同 id 记录（一轮内多次 chat.final 落盘：段落草稿+累计快照）读侧 last-wins 去重。

    不去重时前端按 id 只能命中首条，其余按内容匹配不上沦为独立消息，
    一轮内容按前缀逐段重复展示。
    """
    monkeypatch.setattr(session_history, "get_agent_sessions_dir", lambda: tmp_path)
    session_id = "sess_dedup"
    session_dir = tmp_path / session_id
    session_dir.mkdir(parents=True)
    records = [
        {"id": "r1:user", "role": "user", "content": "问题", "timestamp": 1.0},
        {"id": "r1:assistant", "role": "assistant", "content": "段落一", "event_type": "chat.final", "timestamp": 2.0},
        {"id": "r1:assistant", "role": "assistant", "content": "", "event_type": "chat.tool_call", "timestamp": 2.5},
        {"id": "r1:assistant", "role": "assistant", "content": "", "event_type": "chat.tool_result", "timestamp": 2.6},
        {"id": "member-final-m1-1:assistant", "role": "assistant", "content": "成员输出", "event_type": "chat.final", "timestamp": 3.0},
        {"id": "r1:assistant", "role": "assistant", "content": "段落一段落二", "event_type": "chat.final", "timestamp": 4.0},
        {"id": "r1:assistant", "role": "assistant", "content": "段落一段落二段落三（最全）", "event_type": "chat.final", "timestamp": 5.0},
        {"id": "", "role": "assistant", "content": "无 id 记录保留", "timestamp": 6.0},
    ]
    (session_dir / "history.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )

    loaded = session_history.load_history_records(session_id)

    contents = [r["content"] for r in loaded]
    # chat.final 同 id 收拢为最全一条；tool_call/tool_result 同 id 不收（正常形态）；
    # member-final 独立 id 不动；无 id 记录原样保留
    assert contents == [
        "问题",
        "",
        "",
        "成员输出",
        "段落一段落二段落三（最全）",
        "无 id 记录保留",
    ]
    # last-wins：同 id 取时间戳最末的记录
    assistant = [r for r in loaded if r.get("id") == "r1:assistant" and r.get("event_type") == "chat.final"]
    assert len(assistant) == 1
    assert assistant[0]["timestamp"] == 5.0


def test_chat_error_records_are_restorable():
    """chat.error 落盘记录（模型 401/连接失败等错误轮）重启后必须可恢复——
    不在白名单时错误轮只剩前端本地台账的「失败」折叠区，错误正文丢失。"""
    from jiuwenswarm.server.agent_ws_server import _is_restorable_history_record
    from jiuwenswarm.server.wire_truncate import _HISTORY_RESTORABLE_ASSISTANT_EVENT_TYPES

    assert "chat.error" in _HISTORY_RESTORABLE_ASSISTANT_EVENT_TYPES
    assert _is_restorable_history_record(
        {"role": "leader", "event_type": "chat.error", "content": "[181001] model call failed"}
    )
    # 回归：非白名单事件类型仍被过滤
    assert not _is_restorable_history_record(
        {"role": "assistant", "event_type": "chat.delta", "content": "流式中间帧"}
    )
