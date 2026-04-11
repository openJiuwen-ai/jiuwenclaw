"""会话元数据管理模块"""
from __future__ import annotations

import json
import logging
import queue
import threading
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

from jiuwenclaw.utils import get_agent_sessions_dir

logger = logging.getLogger(__name__)

# ---------- 异步写入队列(与 session_history 保持一致的模式) ----------
_METADATA_QUEUE: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=5000)
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()
_FILE_LOCK = threading.Lock()

# 内存缓存: 解决异步写入时读取到陈旧磁盘数据的竞态条件
_METADATA_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()

# 会话标题自动生成的截取长度
_TITLE_MAX_LEN = 50


def _current_timestamp() -> float:
    """返回显式使用 UTC 时区的当前时间戳"""
    return datetime.now(timezone.utc).timestamp()


def _metadata_file(session_id: str) -> Path:
    """获取会话元数据文件路径"""
    session_dir = get_agent_sessions_dir() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / "metadata.json"


def _read_metadata(session_id: str) -> dict[str, Any]:
    """读取会话元数据(优先从内存缓存读取,避免异步写入未落盘时读到陈旧数据)"""
    with _CACHE_LOCK:
        cached = _METADATA_CACHE.get(session_id)
        if cached is not None:
            return cached.copy()
    fpath = _metadata_file(session_id)
    if not fpath.exists():
        return {}
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception as exc:
        logger.warning("读取 metadata.json 失败: %s", exc)
    return {}


def _write_metadata_sync(session_id: str, metadata: dict[str, Any]) -> None:
    """同步写入会话元数据(由后台 worker 或 fallback 调用)

    注意: 不更新 _METADATA_CACHE。缓存仅由 _enqueue_write 维护,
    避免 gateway 进程的 init_session_metadata 污染缓存导致后续
    读取不到 agentserver 进程写入的最新数据。
    """
    fpath = _metadata_file(session_id)
    with _FILE_LOCK:
        fpath.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def _ensure_worker_started() -> None:
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return

        def _worker() -> None:
            while True:
                sid, metadata = _METADATA_QUEUE.get()
                try:
                    _write_metadata_sync(sid, metadata)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("metadata 异步写入失败: %s", exc)
                finally:
                    _METADATA_QUEUE.task_done()

        t = threading.Thread(target=_worker, name="session-metadata-writer", daemon=True)
        t.start()
        _WORKER_STARTED = True


def _enqueue_write(session_id: str, metadata: dict[str, Any]) -> None:
    """将写入操作放入异步队列,队列满时退化为同步写"""
    # 立即更新缓存,确保后续读取能看到最新状态
    with _CACHE_LOCK:
        _METADATA_CACHE[session_id] = metadata.copy()
    _ensure_worker_started()
    try:
        _METADATA_QUEUE.put_nowait((session_id, metadata))
    except queue.Full:
        _write_metadata_sync(session_id, metadata)


def _auto_title(content: str) -> str:
    """从首条用户消息自动生成会话标题"""
    title = content.strip().replace("\n", " ")
    if len(title) > _TITLE_MAX_LEN:
        title = title[:_TITLE_MAX_LEN] + "..."
    return title


def init_session_metadata(
    *,
    session_id: str,
    channel_id: str = "",
    user_id: str = "",
    title: str = "",
) -> None:
    """初始化会话元数据(同步写,确保创建后立即可读)"""
    metadata = {
        "session_id": session_id,
        "channel_id": channel_id,
        "user_id": user_id,
        "created_at": _current_timestamp(),
        "last_message_at": _current_timestamp(),
        "title": title,
        "message_count": 0,
    }
    _write_metadata_sync(session_id, metadata)


def update_session_metadata(
    *,
    session_id: str,
    channel_id: str | None = None,
    user_id: str | None = None,
    title: str | None = None,
    increment_message_count: bool = False,
    user_content: str | None = None,
    channel_metadata: dict[str, Any] | None = None,
) -> None:
    """更新会话元数据(异步写入,不阻塞调用方)"""
    metadata = _read_metadata(session_id)

    if not metadata:
        # 如果元数据不存在,创建新的(外部渠道隐式创建 session 的兜底)
        # 自动生成标题: 当 title 为空且提供了用户消息内容时
        auto_title = ""
        if not title and user_content:
            auto_title = _auto_title(user_content)
        metadata = {
            "session_id": session_id,
            "channel_id": channel_id or "",
            "user_id": user_id or "",
            "created_at": _current_timestamp(),
            "last_message_at": _current_timestamp(),
            "title": title or auto_title,
            "message_count": 1 if increment_message_count else 0,
        }
        # 首次创建时写入 channel_metadata
        if channel_metadata:
            metadata["channel_metadata"] = channel_metadata
    else:
        # 更新现有元数据
        if channel_id is not None:
            metadata["channel_id"] = channel_id
        if user_id is not None:
            metadata["user_id"] = user_id
        # 仅当传入非空 title 时才更新（防止空字符串覆盖已有标题）
        if title:
            metadata["title"] = title
        if increment_message_count:
            metadata["message_count"] = metadata.get("message_count", 0) + 1

        # 自动生成标题: 当 title 为空且提供了用户消息内容时
        if not metadata.get("title") and user_content:
            metadata["title"] = _auto_title(user_content)

        # channel_metadata 仅在首次为空时补充写入（不覆盖）
        if channel_metadata and not metadata.get("channel_metadata"):
            metadata["channel_metadata"] = channel_metadata

        # 总是更新最后消息时间
        metadata["last_message_at"] = _current_timestamp()

    _enqueue_write(session_id, metadata)


def get_session_metadata(session_id: str) -> dict[str, Any]:
    """获取会话元数据"""
    return _read_metadata(session_id)


def get_all_sessions_metadata(
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """
    获取所有会话的元数据。

    Returns:
        (sessions, total): 当前页的会话列表 和 会话总数
    """
    sessions_dir = get_agent_sessions_dir()
    if not sessions_dir.exists() or not sessions_dir.is_dir():
        return [], 0

    sessions = []
    for session_dir in sessions_dir.iterdir():
        if not session_dir.is_dir():
            continue

        session_id = session_dir.name
        metadata = _read_metadata(session_id)

        if not metadata:
            # 没有 metadata.json 的旧会话: 只构造最小信息,不读取 history.json
            # (避免大量旧会话导致接口变慢,完整推断由启动迁移负责)
            metadata = {
                "session_id": session_id,
                "channel_id": "",
                "user_id": "",
                "created_at": session_dir.stat().st_ctime,
                "last_message_at": session_dir.stat().st_mtime,
                "title": "",
                "message_count": 0,
            }

        sessions.append(metadata)

    # 按最后消息时间倒序排序
    sessions.sort(key=lambda x: x.get("last_message_at", 0), reverse=True)

    total = len(sessions)
    return sessions[offset: offset + limit], total
