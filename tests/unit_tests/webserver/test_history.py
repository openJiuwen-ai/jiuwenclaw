# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json

import pytest

from jiuwenclaw.webserver.history import (
    ChatHistoryStore,
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
    assert s["last_preview"].startswith("你好，有什么")

    detail = await store.get_session_detail("s1")
    assert detail is not None
    assert len(detail["messages"]) == 2
    assert detail["messages"][0]["role"] == "user"
    assert detail["messages"][0]["content"] == "你好"
    assert detail["messages"][1]["role"] == "assistant"
    await store.close()


@pytest.mark.asyncio
async def test_record_idempotent_on_resend(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    inserted1 = await store.record_user(request_id="r1", session_id="s1", query="hello", ts=1000.0)
    inserted2 = await store.record_user(request_id="r1", session_id="s1", query="hello", ts=1000.0)
    assert inserted1 is True
    assert inserted2 is False  # 幂等命中
    sessions = await store.list_sessions()
    assert sessions[0]["message_count"] == 1  # 不重复计数
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
    assert detail["title"] == "第一条用户消息"  # 首条 user 设定，assistant 不覆盖
    await store.close()


@pytest.mark.asyncio
async def test_callback_whitelist_ignores_non_chat(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    cb = make_history_callback(store)

    await cb("browser", json.dumps({  # skilldev.* 不在白名单
        "type": "req", "id": "x1", "method": "skilldev.start", "params": {"query": "应被忽略"},
    }))
    await cb("browser", json.dumps({  # chat.interrupt 不在白名单
        "type": "req", "id": "x2", "method": "chat.interrupt", "params": {"query": "应被忽略"},
    }))

    assert await store.list_sessions() == []
    await store.close()


@pytest.mark.asyncio
async def test_callback_user_with_session_id_records_directly(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    cb = make_history_callback(store)
    await cb("browser", json.dumps({
        "type": "req", "id": "r1", "method": "chat.send",
        "params": {"session_id": "s1", "query": "直接落盘"},
    }))
    detail = await store.get_session_detail("s1")
    assert detail is not None
    assert len(detail["messages"]) == 1
    assert detail["messages"][0]["role"] == "user"
    await store.close()


@pytest.mark.asyncio
async def test_callback_pending_backfill_on_final(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    cb = make_history_callback(store)

    # 首条 chat.send 无 session_id → 暂存 pending，尚未落盘
    await cb("browser", json.dumps({
        "type": "req", "id": "r1", "method": "chat.send", "params": {"query": "在吗"},
    }))
    assert await store.list_sessions() == []

    # final 带回 session_id → 回填 user + 落 assistant
    await cb("uplink", json.dumps({
        "type": "event", "event": "chat.final", "request_id": "r1",
        "payload": {"session_id": "s1", "content": "在的"},
    }))
    detail = await store.get_session_detail("s1")
    assert detail is not None
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["messages"][0]["content"] == "在吗"
    assert detail["messages"][1]["content"] == "在的"
    await store.close()


@pytest.mark.asyncio
async def test_callback_ignores_delta_events(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    cb = make_history_callback(store)
    await cb("uplink", json.dumps({
        "type": "event", "event": "chat.tool_calls.delta", "request_id": "r1",
        "payload": {"session_id": "s1", "content": "增量片段"},
    }))
    assert await store.list_sessions() == []  # 中间事件不采集
    await store.close()


@pytest.mark.asyncio
async def test_callback_records_chat_error(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    cb = make_history_callback(store)
    await cb("browser", json.dumps({
        "type": "req", "id": "r1", "method": "chat.send",
        "params": {"session_id": "s1", "query": "出错了"},
    }))
    await cb("uplink", json.dumps({
        "type": "event", "event": "chat.error", "request_id": "r1",
        "payload": {"session_id": "s1", "error": "内部错误"},
    }))
    detail = await store.get_session_detail("s1")
    assert detail is not None
    assistant_msgs = [m for m in detail["messages"] if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["event_type"] == "chat.error"
    assert assistant_msgs[0]["content"] == "内部错误"
    await store.close()


@pytest.mark.asyncio
async def test_callback_invalid_json_ignored(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    cb = make_history_callback(store)
    await cb("browser", "不是JSON", "conn1")  # 不应抛异常
    assert await store.list_sessions() == []
    await store.close()


@pytest.mark.asyncio
async def test_callback_final_without_session_id_dropped(tmp_path) -> None:
    store = ChatHistoryStore(tmp_path / "h.db")
    cb = make_history_callback(store)
    await cb("uplink", json.dumps({
        "type": "event", "event": "chat.final", "request_id": "r1",
        "payload": {"content": "缺 sid 应丢弃"},
    }))
    assert await store.list_sessions() == []
    await store.close()
