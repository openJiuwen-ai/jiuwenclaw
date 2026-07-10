# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import logging
import queue
import threading
from pathlib import Path
from typing import Any

from jiuwenclaw.utils import get_agent_sessions_dir
from jiuwenclaw.perf.guard import run_perf_safe

logger = logging.getLogger(__name__)

_FILE_LOCK = threading.Lock()
_WRITE_QUEUE: queue.Queue[tuple[Path, dict[str, Any]]] = queue.Queue(maxsize=20000)
_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


def request_summaries_file(session_id: str, sessions_root: str | None = None) -> Path:
    root = Path(sessions_root) if sessions_root else get_agent_sessions_dir()
    session_dir = root / (session_id or "default")
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / "request_summaries.jsonl"


def _write_item(path: Path, item: dict[str, Any]) -> None:
    with _FILE_LOCK:
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(item, ensure_ascii=False))
            fh.write("\n")


def _ensure_worker_started() -> None:
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return

        def _worker() -> None:
            while True:
                path, item = _WRITE_QUEUE.get()
                run_perf_safe(
                    "RequestSummaryWriter",
                    "async write",
                    lambda: _write_item(path, item),
                )
                _WRITE_QUEUE.task_done()

        thread = threading.Thread(
            target=_worker,
            name="request-summary-writer",
            daemon=True,
        )
        thread.start()
        _WORKER_STARTED = True


def flush_request_summary_writer(timeout: float = 5.0) -> None:
    """Block until queued summary writes complete, or until timeout elapses."""
    if not _WORKER_STARTED:
        return
    import time

    deadline = time.monotonic() + max(0.0, timeout)
    while _WRITE_QUEUE.unfinished_tasks > 0:
        if time.monotonic() >= deadline:
            logger.warning(
                "[RequestSummaryWriter] flush timed out with %d pending writes",
                _WRITE_QUEUE.unfinished_tasks,
            )
            return
        time.sleep(0.05)


def append_request_summary(
    session_id: str,
    summary: dict[str, Any],
    *,
    sessions_root: str | None = None,
) -> None:
    """Append one request summary line to request_summaries.jsonl."""
    sid = (session_id or "default").strip() or "default"
    path = request_summaries_file(sid, sessions_root)
    _ensure_worker_started()
    try:
        _WRITE_QUEUE.put_nowait((path, summary))
    except queue.Full:
        _write_item(path, summary)
