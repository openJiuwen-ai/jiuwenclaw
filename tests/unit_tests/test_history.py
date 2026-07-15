# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json

import pytest

from jiuwenclaw.history_store import (
    ChatHistoryStore,
    get_session_detail_sync,
    list_sessions_sync,
    make_history_callback,
)


@pytest.mark.asyncio
async def test_record_user_and_assistant_then_list_detail(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    await store.record_user(request_id="r1", session_id="s1", query="你好", ts=1000.0)
    await store.record_assistant(
        request_id="r1", session_id="s1", content="你好，有什么可以帮你？",
        event_type="chat.final", ts=1001.0,
    )
    sessions = await store.list_sessions()
    assert len(sessions) == 1
    s = sessions[0]
    assert s["session_id"] == "s1"
    assert s["title"] == "你好"
    assert s["message_count"] == 2
    detail = await store.get_session_detail("s1")
    assert detail is not None
    assert len(detail["messages"]) == 2
    assert detail["messages"][0]["role"] == "user"
    await store.close()


@pytest.mark.asyncio
async def test_record_idempotent_on_resend(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    inserted1 = await store.record_user(request_id="r1", session_id="s1", query="hello", ts=1000.0)
    inserted2 = await store.record_user(request_id="r1", session_id="s1", query="hello", ts=1000.0)
    assert inserted1 is True
    assert inserted2 is False
    sessions = await store.list_sessions()
    assert sessions[0]["message_count"] == 1
    await store.close()


@pytest.mark.asyncio
async def test_title_first_set_not_overwritten(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    await store.record_user(request_id="r1", session_id="s1", query="第一条用户消息", ts=1000.0)
    await store.record_assistant(
        request_id="r1", session_id="s1", content="回复内容",
        event_type="chat.final", ts=1001.0,
    )
    detail = await store.get_session_detail("s1")
    assert detail is not None
    assert detail["title"] == "第一条用户消息"
    await store.close()


@pytest.mark.asyncio
async def test_callback_whitelist_ignores_non_chat(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    cb = make_history_callback(store)
    await cb("browser", json.dumps({"type": "req", "id": "x1", "method": "skilldev.start", "params": {"query": "应被忽略"}}))
    await cb("browser", json.dumps({"type": "req", "id": "x2", "method": "chat.interrupt", "params": {"query": "应被忽略"}}))
    assert await store.list_sessions() == []
    await store.close()


@pytest.mark.asyncio
async def test_callback_user_with_session_id_records_directly(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    cb = make_history_callback(store)
    await cb("browser", json.dumps({"type": "req", "id": "r1", "method": "chat.send", "params": {"session_id": "s1", "query": "直接落盘"}}))
    detail = await store.get_session_detail("s1")
    assert detail is not None
    assert len(detail["messages"]) == 1
    await store.close()


@pytest.mark.asyncio
async def test_callback_pending_backfill_on_final(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    cb = make_history_callback(store)
    await cb("browser", json.dumps({"type": "req", "id": "r1", "method": "chat.send", "params": {"query": "在吗"}}))
    assert await store.list_sessions() == []
    # 流式增量 chat.delta 逐帧累积
    await cb("uplink", json.dumps({"type": "event", "event": "chat.delta", "request_id": "r1", "payload": {"session_id": "s1", "content": "在"}}))
    await cb("uplink", json.dumps({"type": "event", "event": "chat.delta", "request_id": "r1", "payload": {"session_id": "s1", "content": "的"}}))
    # chat.final 终止信号，content 为空
    await cb("uplink", json.dumps({"type": "event", "event": "chat.final", "request_id": "r1", "payload": {"session_id": "s1", "content": ""}}))
    detail = await store.get_session_detail("s1")
    assert detail is not None
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["messages"][1]["content"] == "在的"  # delta 累积
    await store.close()


@pytest.mark.asyncio
async def test_callback_ignores_delta_events(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    cb = make_history_callback(store)
    await cb("uplink", json.dumps({"type": "event", "event": "chat.tool_calls.delta", "request_id": "r1", "payload": {"session_id": "s1", "content": "增量"}}))
    assert await store.list_sessions() == []
    await store.close()


@pytest.mark.asyncio
async def test_callback_records_chat_error(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    cb = make_history_callback(store)
    await cb("browser", json.dumps({"type": "req", "id": "r1", "method": "chat.send", "params": {"session_id": "s1", "query": "出错了"}}))
    await cb("uplink", json.dumps({"type": "event", "event": "chat.error", "request_id": "r1", "payload": {"session_id": "s1", "error": "内部错误"}}))
    detail = await store.get_session_detail("s1")
    assert detail is not None
    assistant_msgs = [m for m in detail["messages"] if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["event_type"] == "chat.error"
    await store.close()


@pytest.mark.asyncio
async def test_callback_invalid_json_ignored(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    cb = make_history_callback(store)
    await cb("browser", "不是JSON", "conn1")
    assert await store.list_sessions() == []
    await store.close()


@pytest.mark.asyncio
async def test_callback_final_without_session_id_dropped(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    cb = make_history_callback(store)
    await cb("uplink", json.dumps({"type": "event", "event": "chat.final", "request_id": "r1", "payload": {"content": "缺 sid"}}))
    assert await store.list_sessions() == []
    await store.close()


# ---- 同步只读（供 http.server 的 _SpaStaticHandler 调用）----

@pytest.mark.asyncio
async def test_sync_read_after_write(tmp_path) -> None:
    db = tmp_path / "h.db"
    store = ChatHistoryStore(db)
    await store.record_user(request_id="r1", session_id="s1", query="问题", ts=1000.0)
    await store.record_assistant(
        request_id="r1", session_id="s1", content="回答",
        event_type="chat.final", ts=1001.0,
    )
    await store.close()  # 确保 aiosqlite 写入落盘

    sessions = list_sessions_sync(db)
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "s1"

    detail = get_session_detail_sync(db, "s1")
    assert detail is not None
    assert len(detail["messages"]) == 2

    assert get_session_detail_sync(db, "nope") is None
    # db 不存在：返回空 / None，不抛
    assert list_sessions_sync(tmp_path / "missing.db") == []
    assert get_session_detail_sync(tmp_path / "missing.db", "x") is None
