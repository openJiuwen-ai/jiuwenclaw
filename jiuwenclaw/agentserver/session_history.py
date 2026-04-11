from __future__ import annotations

import logging
import json
import queue
import threading
from pathlib import Path
from typing import Any

from jiuwenclaw.utils import get_agent_sessions_dir


logger = logging.getLogger(__name__)
_FILE_LOCK = threading.Lock()
_WRITE_QUEUE: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=20000)
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


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
        item.update(extra)

    _ensure_worker_started()
    try:
        _WRITE_QUEUE.put_nowait((sid, item))
    except queue.Full:
        # 队列满时退化为同步写，避免丢历史记录。
        _write_item(sid, item)

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
        )
    except Exception as exc:
        logger.warning("更新会话元数据失败: %s", exc)
