from __future__ import annotations

import datetime
import logging
import json
import queue
import threading
from pathlib import Path
from typing import Any

from jiuwenclaw.utils import get_agent_sessions_dir


logger = logging.getLogger(__name__)
_FILE_LOCK = threading.Lock()
_WRITE_QUEUE: queue.Queue[tuple[str, dict[str, Any], str | None]] = queue.Queue(maxsize=20000)
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


def _history_file(session_id: str, sessions_root: str | None = None) -> Path:
    root = Path(sessions_root) if sessions_root else get_agent_sessions_dir()
    session_dir = root / session_id
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


def _write_item(session_id: str, item: dict[str, Any], sessions_root: str | None = None) -> None:
    fpath = _history_file(session_id, sessions_root)
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
                sid, item, sessions_root = _WRITE_QUEUE.get()
                try:
                    _write_item(sid, item, sessions_root)
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
    sessions_root: str | Path | None = None,
) -> None:
    """向指定 session 的 history.json 异步追加一条记录."""
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

    _ensure_worker_started()
    sessions_root_s = str(sessions_root) if sessions_root else None
    try:
        _WRITE_QUEUE.put_nowait((sid, item, sessions_root_s))
    except queue.Full:
        # 队列满时退化为同步写，避免丢历史记录。
        _write_item(sid, item, sessions_root_s)

    # 更新会话元数据
    try:
        from jiuwenclaw.agentserver.session_metadata import update_session_metadata
        update_session_metadata(
            session_id=sid,
            channel_id=cid,
            increment_message_count=True,
            # 传入用户消息内容,用于自动生成标题
            user_content=content_text if role_norm == "user" else None,
            # 传入渠道元数据,首次写入时持久化
            channel_metadata=channel_metadata,
            mode=mode,
            sessions_root=sessions_root_s,
        )
    except Exception as exc:
        logger.warning("更新会话元数据失败: %s", exc)
