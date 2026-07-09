from __future__ import annotations

import datetime
import logging
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any

from jiuwenavatar.common.utils import get_agent_sessions_dir


logger = logging.getLogger(__name__)
_FILE_LOCK = threading.Lock()
_WRITE_QUEUE: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=20000)
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


def _serialize_value(obj: Any) -> Any:
    """将对象转换为 JSON 可序列化的格式."""
    if isinstance(obj, datetime.datetime):
        return obj.isoformat()
    if isinstance(obj, datetime.date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize_value(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize_value(item) for item in obj]
    return obj


def _history_file(session_id: str) -> Path:
    session_dir = get_agent_sessions_dir() / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / "history.json"


def _read_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取 history.json 失败，已忽略并重建: %s", exc)
        return []
    if isinstance(data, list):
        return data
    return []


_TEAM_RELEVANT_EVENT_TYPES = frozenset({
    "team.message",
    "team.member",
    "team.task",
    "team.event",
    "chat.tool_call", "chat.tracer_agent",
    "chat.final", "chat.tool_result", "chat.file",
})


def _is_team_relevant(item: dict[str, Any]) -> bool:
    et = item.get("event_type")
    if not isinstance(et, str):
        return False
    if et in _TEAM_RELEVANT_EVENT_TYPES:
        if et in ("chat.tool_call", "chat.tracer_agent"):
            mode = item.get("mode")
            return isinstance(mode, str) and mode.strip().lower() == "team"
        if et in ("chat.final", "chat.tool_result", "chat.file"):
            role = item.get("role")
            return isinstance(role, str) and role.strip().lower() == "teammate"
        return True
    return False


def read_team_history_records(session_id: str) -> list[dict[str, Any]]:
    """读取指定会话的历史记录，仅返回 team 模式相关的记录。"""
    fpath = _history_file(session_id)
    all_records = _read_history(fpath)
    # write_text 非原子写入（先截断再写入），读取可能命中截断窗口，
    # 用递增间隔重试最多 5 次等待写入完成
    if not all_records and fpath.exists():
        for attempt in range(1, 6):
            time.sleep(0.2 * attempt)
            all_records = _read_history(fpath)
            if all_records:
                logger.info("read_team_history_records: recovered on retry %d", attempt)
                break
        if not all_records:
            logger.warning(
                "read_team_history_records: all retries exhausted, file_size=%d",
                fpath.stat().st_size,
            )

    return [item for item in all_records if isinstance(item, dict) and _is_team_relevant(item)]


def _write_item(session_id: str, item: dict[str, Any]) -> None:
    fpath = _history_file(session_id)
    with _FILE_LOCK:
        history = _read_history(fpath)
        history.append(item)
        fpath.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
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
                sid, item = _WRITE_QUEUE.get()
                try:
                    _write_item(sid, item)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("history 异步写入失败: %s", exc)
                finally:
                    _WRITE_QUEUE.task_done()

        t = threading.Thread(target=_worker, name="session-history-writer", daemon=True)
        t.start()
        _WORKER_STARTED = True


def append_history_record(
    *,
    session_id: str,
    request_id: str,
    channel_id: str,
    role: str,
    content: Any,
    timestamp: float,
    event_type: str | None = None,
    extra: dict[str, Any] | None = None,
    channel_metadata: dict[str, Any] | None = None,
    mode: str | None = None,
    avatar_id: str | None = None,
    media_items: list[dict[str, Any]] | None = None,
) -> None:
    """向指定 session 的 history.json 异步追加一条记录.

    media_items: 可选，媒体附件列表，每项包含 type/mimeType/filename/path。
                 图片保存到 session 目录下 media/ 子目录，path 为相对路径。
    """
    sid = (session_id or "default").strip() or "default"
    rid = str(request_id or "").strip()
    cid = str(channel_id or "").strip()
    role_norm = "assistant" if role == "assistant" else "user"
    content_text = content if isinstance(content, str) else str(content)

    item: dict[str, Any] = {
        "id": f"{rid}:{role_norm}",
        "role": role_norm,
        "request_id": rid,
        "channel_id": cid,
        "timestamp": float(timestamp),
        "content": content_text,
    }
    if role_norm == "assistant" and event_type:
        item["event_type"] = event_type
    if isinstance(extra, dict) and extra:
        serialized_extra = _serialize_value(extra)
        if isinstance(serialized_extra, dict):
            item.update(serialized_extra)
    if mode:
        item["mode"] = str(mode)
    if media_items:
        item["media_items"] = _serialize_value(media_items)

    _ensure_worker_started()
    try:
        _WRITE_QUEUE.put_nowait((sid, item))
    except queue.Full:
        # 队列满时退化为同步写，避免丢历史记录。
        _write_item(sid, item)

    # 更新会话元数据
    try:
        from jiuwenavatar.server.runtime.session.session_metadata import (
            set_session_delivery_context,
            update_session_metadata,
        )
        update_session_metadata(
            session_id=sid,
            channel_id=cid,
            increment_message_count=True,
            # 传入用户消息内容,用于自动生成标题
            user_content=content_text if role_norm == "user" else None,
            # 传入渠道元数据,首次写入时持久化
            channel_metadata=channel_metadata,
            mode=mode,
            # 首条消息兜底补绑分身（仅在 metadata 未绑定时写入）
            avatar_id=avatar_id,
        )
        if role_norm == "user":
            set_session_delivery_context(
                session_id=sid,
                channel_id=cid,
                source_request_id=rid,
                route_metadata=channel_metadata,
            )
    except Exception as exc:
        logger.warning("更新会话元数据失败: %s", exc)


def append_compact_history_records(
    *,
    session_id: str,
    request_id: str,
    channel_id: str,
    summary: str | None,
    timestamp: float,
    trigger: str = "auto",
    stats: dict[str, Any] | None = None,
    mode: str | None = None,
) -> None:
    """Persist a compact boundary and optional transcript-only summary."""
    clean_summary = (summary or "").strip()
    metadata = {
        "compact_metadata": {
            "trigger": trigger,
            **(_serialize_value(stats) if isinstance(stats, dict) else {}),
        },
    }

    append_history_record(
        session_id=session_id,
        request_id=request_id,
        channel_id=channel_id,
        role="assistant",
        event_type="context.compact_boundary",
        content="Conversation compacted",
        timestamp=timestamp,
        extra=metadata,
        mode=mode,
    )

    if not clean_summary:
        return

    append_history_record(
        session_id=session_id,
        request_id=request_id,
        channel_id=channel_id,
        role="assistant",
        event_type="context.compact_summary",
        content=clean_summary,
        timestamp=timestamp + 0.001,
        extra={
            **metadata,
            "is_compact_summary": True,
            "transcript_only": True,
        },
        mode=mode,
    )


def truncate_history_records(*, session_id: str, cut_index: int) -> dict[str, Any]:
    """截断会话历史到指定位置（线程安全）。

    先等待异步写入队列刷盘，再持锁截断 history.json。
    返回截断结果 dict，包含 remaining / removed 计数。
    """
    sid = (session_id or "default").strip() or "default"
    _WRITE_QUEUE.join()

    fpath = _history_file(sid)
    with _FILE_LOCK:
        if not fpath.exists():
            return {"remaining_records": 0, "removed_records": 0}
        history = _read_history(fpath)
        if not isinstance(history, list):
            return {"remaining_records": 0, "removed_records": 0}
        total = len(history)
        if cut_index < 0:
            cut_index = 0
        if cut_index > total:
            cut_index = total
        truncated = history[:cut_index]
        fpath.write_text(
            json.dumps(truncated, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "remaining_records": len(truncated),
            "removed_records": total - len(truncated),
        }
