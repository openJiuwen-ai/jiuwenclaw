# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved

"""Web 会话历史公开同步 API（http.server / fastapi 线程用）。"""

from __future__ import annotations

import threading
from typing import Any

from .store import ChatHistoryStore

_default_store: ChatHistoryStore | None = None
_default_store_lock = threading.Lock()


def get_default_store() -> ChatHistoryStore:
    """进程内单例，避免 HTTP 每次请求重建库。"""
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = ChatHistoryStore.from_env()
        return _default_store


def set_default_store(store: ChatHistoryStore) -> None:
    global _default_store
    with _default_store_lock:
        _default_store = store


def _coerce_store(store: Any | None) -> ChatHistoryStore | None:
    if store is None:
        st = get_default_store()
        return st if st.available else None
    if isinstance(store, ChatHistoryStore):
        return store if store.available else None
    return None


def list_sessions_sync(
    store: ChatHistoryStore | None = None,
    *,
    limit: int = 20,
    offset: int = 0,
    user: str | None = None,
) -> list[dict[str, Any]]:
    """同步读会话列表（http.server 线程用）。库不可用返回空。"""
    st = _coerce_store(store)
    if st is None:
        return []
    return st.list_sessions_blocking(limit=limit, offset=offset, user=user)


def get_session_detail_sync(
    session_id: str,
    store: ChatHistoryStore | None = None,
    *,
    user: str | None = None,
) -> dict[str, Any] | None:
    """同步读会话详情。"""
    if not session_id:
        return None
    st = _coerce_store(store)
    if st is None:
        return None
    return st.get_session_detail_blocking(str(session_id), user=user)


def count_sessions_sync(
    store: ChatHistoryStore | None = None,
    *,
    user: str | None = None,
) -> int:
    """同步统计会话总数（分页 total 用）。

    与 list_sessions_sync 不同：store/DB 不可用时抛异常，调用方据此区分
    "确实为空"与"库故障"。
    """
    st = _coerce_store(store)
    if st is None:
        raise RuntimeError("web history store unavailable")
    return st.count_sessions_blocking(user=user)


def get_session_detail_strict_sync(
    session_id: str,
    store: ChatHistoryStore | None = None,
    *,
    user: str | None = None,
) -> dict[str, Any] | None:
    """同步读会话详情（严格版）：store/DB 故障抛异常，None 仅表示会话不存在。"""
    if not session_id:
        return None
    st = _coerce_store(store)
    if st is None:
        raise RuntimeError("web history store unavailable")
    return st.get_session_detail_strict_blocking(str(session_id), user=user)


def delete_session_sync(
    session_id: str,
    store: ChatHistoryStore | None = None,
    *,
    user: str | None = None,
) -> bool:
    """同步删除会话（sessions 行 + messages 行）。"""
    if not session_id:
        return False
    st = _coerce_store(store)
    if st is None:
        return False
    return st.delete_session_blocking(str(session_id), user=user)


def set_session_pinned_sync(
    session_id: str,
    pinned: bool,
    store: ChatHistoryStore | None = None,
    *,
    user: str | None = None,
) -> tuple[bool, int] | None:
    """同步置顶/取消置顶会话（remote 模式 session.pin 用）。

    严格版语义：store/DB 不可用抛异常（调用方区分故障与不存在）；
    会话不存在返回 ``None``；成功返回 ``(pinned, pin_order)``。
    """
    if not session_id:
        return None
    st = _coerce_store(store)
    if st is None:
        raise RuntimeError("web history store unavailable")
    return st.set_session_pinned_blocking(str(session_id), bool(pinned), user=user)


def list_pinned_sessions_sync(
    store: ChatHistoryStore | None = None,
    *,
    user: str | None = None,
) -> list[dict[str, Any]]:
    """同步读全部置顶会话（remote 模式 project.pinned_sessions 用）。库不可用返回空。"""
    st = _coerce_store(store)
    if st is None:
        return []
    return st.list_pinned_sessions_blocking(user=user)
