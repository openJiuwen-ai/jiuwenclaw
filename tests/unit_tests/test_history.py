# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import json

import pytest

from jiuwenswarm.channels.web.history_store import (
    ChatHistoryStore,
    get_session_detail_sync,
    list_sessions_sync,
    make_history_callback,
    resolve_history_db_type,
    set_default_store,
)


def _mem_store() -> ChatHistoryStore:
    return ChatHistoryStore.memory()


@pytest.mark.asyncio
async def test_record_user_and_assistant_then_list_detail() -> None:
    store = _mem_store()
    await store.record_user(request_id="r1", session_id="s1", query="你好", ts=1000.0)
    await store.record_assistant(
        request_id="r1",
        session_id="s1",
        content="你好，有什么可以帮你？",
        event_type="chat.final",
        ts=1001.0,
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
async def test_record_idempotent_on_resend() -> None:
    store = _mem_store()
    inserted1 = await store.record_user(
        request_id="r1", session_id="s1", query="hello", ts=1000.0
    )
    inserted2 = await store.record_user(
        request_id="r1", session_id="s1", query="hello", ts=1000.0
    )
    assert inserted1 is True
    assert inserted2 is False
    sessions = await store.list_sessions()
    assert sessions[0]["message_count"] == 1
    await store.close()


@pytest.mark.asyncio
async def test_title_first_set_not_overwritten() -> None:
    store = _mem_store()
    await store.record_user(
        request_id="r1", session_id="s1", query="第一条用户消息", ts=1000.0
    )
    await store.record_assistant(
        request_id="r1",
        session_id="s1",
        content="回复内容",
        event_type="chat.final",
        ts=1001.0,
    )
    detail = await store.get_session_detail("s1")
    assert detail is not None
    assert detail["title"] == "第一条用户消息"
    await store.close()


@pytest.mark.asyncio
async def test_callback_whitelist_ignores_non_chat() -> None:
    store = _mem_store()
    cb = make_history_callback(store)
    await cb(
        "browser",
        json.dumps(
            {
                "type": "req",
                "id": "x1",
                "method": "skilldev.start",
                "params": {"query": "应被忽略"},
            }
        ),
    )
    await cb(
        "browser",
        json.dumps(
            {
                "type": "req",
                "id": "x2",
                "method": "chat.interrupt",
                "params": {"query": "应被忽略"},
            }
        ),
    )
    assert await store.list_sessions() == []
    await store.close()


@pytest.mark.asyncio
async def test_callback_user_with_session_id_records_directly() -> None:
    store = _mem_store()
    cb = make_history_callback(store)
    await cb(
        "browser",
        json.dumps(
            {
                "type": "req",
                "id": "r1",
                "method": "chat.send",
                "params": {"session_id": "s1", "query": "直接落盘"},
            }
        ),
    )
    detail = await store.get_session_detail("s1")
    assert detail is not None
    assert len(detail["messages"]) == 1
    await store.close()


@pytest.mark.asyncio
async def test_callback_pending_backfill_on_final() -> None:
    store = _mem_store()
    cb = make_history_callback(store)
    await cb(
        "browser",
        json.dumps(
            {
                "type": "req",
                "id": "r1",
                "method": "chat.send",
                "params": {"query": "在吗"},
            }
        ),
    )
    assert await store.list_sessions() == []
    await cb(
        "uplink",
        json.dumps(
            {
                "type": "event",
                "event": "chat.delta",
                "request_id": "r1",
                "payload": {"session_id": "s1", "content": "在"},
            }
        ),
    )
    await cb(
        "uplink",
        json.dumps(
            {
                "type": "event",
                "event": "chat.delta",
                "request_id": "r1",
                "payload": {"session_id": "s1", "content": "的"},
            }
        ),
    )
    await cb(
        "uplink",
        json.dumps(
            {
                "type": "event",
                "event": "chat.final",
                "request_id": "r1",
                "payload": {"session_id": "s1", "content": ""},
            }
        ),
    )
    detail = await store.get_session_detail("s1")
    assert detail is not None
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]
    assert detail["messages"][1]["content"] == "在的"
    await store.close()


@pytest.mark.asyncio
async def test_callback_ignores_delta_events() -> None:
    store = _mem_store()
    cb = make_history_callback(store)
    await cb(
        "uplink",
        json.dumps(
            {
                "type": "event",
                "event": "chat.tool_calls.delta",
                "request_id": "r1",
                "payload": {"session_id": "s1", "content": "增量"},
            }
        ),
    )
    assert await store.list_sessions() == []
    await store.close()


@pytest.mark.asyncio
async def test_callback_records_chat_error() -> None:
    store = _mem_store()
    cb = make_history_callback(store)
    await cb(
        "browser",
        json.dumps(
            {
                "type": "req",
                "id": "r1",
                "method": "chat.send",
                "params": {"session_id": "s1", "query": "出错了"},
            }
        ),
    )
    await cb(
        "uplink",
        json.dumps(
            {
                "type": "event",
                "event": "chat.error",
                "request_id": "r1",
                "payload": {"session_id": "s1", "error": "内部错误"},
            }
        ),
    )
    detail = await store.get_session_detail("s1")
    assert detail is not None
    assistant_msgs = [m for m in detail["messages"] if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["event_type"] == "chat.error"
    await store.close()


@pytest.mark.asyncio
async def test_callback_invalid_json_ignored() -> None:
    store = _mem_store()
    cb = make_history_callback(store)
    await cb("browser", "不是JSON", "conn1")
    assert await store.list_sessions() == []
    await store.close()


@pytest.mark.asyncio
async def test_callback_final_without_session_id_dropped() -> None:
    store = _mem_store()
    cb = make_history_callback(store)
    await cb(
        "uplink",
        json.dumps(
            {
                "type": "event",
                "event": "chat.final",
                "request_id": "r1",
                "payload": {"content": "缺 sid"},
            }
        ),
    )
    assert await store.list_sessions() == []
    await store.close()


@pytest.mark.asyncio
async def test_sync_read_after_write() -> None:
    store = _mem_store()
    set_default_store(store)
    await store.record_user(request_id="r1", session_id="s1", query="问题", ts=1000.0)
    await store.record_assistant(
        request_id="r1",
        session_id="s1",
        content="回答",
        event_type="chat.final",
        ts=1001.0,
    )

    sessions = list_sessions_sync(store)
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "s1"

    detail = get_session_detail_sync("s1", store)
    assert detail is not None
    assert len(detail["messages"]) == 2

    assert get_session_detail_sync("nope", store) is None
    await store.close()


def test_mysql_without_host_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_DB_HOST", raising=False)
    monkeypatch.setenv("WEB_DB_TYPE", "mysql")
    store = ChatHistoryStore.from_env()
    assert store.backend == "mysql"
    assert store.available is False


def test_resolve_history_db_type_prefers_web_db_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEB_DB_TYPE", "postgresql")
    monkeypatch.setenv("DB_TYPE", "mysql")
    monkeypatch.delenv("WEB_DB_HOST", raising=False)
    assert resolve_history_db_type() == "postgresql"


def test_resolve_history_db_type_host_implies_mysql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEB_DB_TYPE", raising=False)
    monkeypatch.delenv("DB_TYPE", raising=False)
    monkeypatch.setenv("WEB_DB_HOST", "127.0.0.1")
    assert resolve_history_db_type() == "mysql"


def test_resolve_history_db_type_follows_deployment_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEB_DB_TYPE", raising=False)
    monkeypatch.delenv("DB_TYPE", raising=False)
    monkeypatch.delenv("WEB_DB_HOST", raising=False)
    monkeypatch.setenv("DEPLOYMENT_MODE", "distributed")
    assert resolve_history_db_type() == "mysql"
    monkeypatch.setenv("DEPLOYMENT_MODE", "standalone")
    assert resolve_history_db_type() == "memory"

@pytest.mark.asyncio
async def test_callback_uplink_request_id_in_payload() -> None:
    store = _mem_store()
    cb = make_history_callback(store)
    await cb(
        "browser",
        json.dumps({"type": "req", "id": "r1", "method": "chat.send", "params": {"query": "hi"}}),
    )
    await cb(
        "uplink",
        json.dumps(
            {
                "type": "event",
                "event": "chat.delta",
                "payload": {"session_id": "s1", "request_id": "r1", "content": "ok"},
            },
        ),
    )
    await cb(
        "uplink",
        json.dumps(
            {
                "type": "event",
                "event": "chat.final",
                "payload": {"session_id": "s1", "request_id": "r1", "content": ""},
            },
        ),
    )
    detail = await store.get_session_detail("s1")
    assert detail is not None
    assert detail["messages"][-1]["content"] == "ok"
    await store.close()


def test_sync_read_missing_sqlite_file(tmp_path) -> None:
    missing = tmp_path / "missing.db"
    assert list_sessions_sync(missing) == []
    assert get_session_detail_sync(missing, "x") is None

