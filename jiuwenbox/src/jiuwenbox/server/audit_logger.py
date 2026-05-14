# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Structured audit logging in JSONL format.

Each sandbox gets its own log file under ~/.jiuwenbox/logs/. Writes are routed
through a dedicated single-thread executor so disk I/O does not stall the
asyncio event loop on hot paths such as ``exec_in_sandbox``.
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from jiuwenbox.logging_config import configure_logging
from jiuwenbox.models.common import AuditEvent, AuditEventType
from jiuwenbox.server.workspace import JIUWENBOX_HOME

configure_logging()
logger = logging.getLogger(__name__)


def _write_jsonl_line(log_file: Path, line: str) -> None:
    """Append ``line`` (already serialized JSON) to ``log_file``.

    POSIX guarantees writes <= ``PIPE_BUF`` (typically 4 KiB) to ``O_APPEND``
    files are atomic, so we can interleave appends from multiple threads
    without corrupting individual JSONL records.
    """
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


class AuditLogger:
    """Append-only JSONL audit logger, one file per sandbox."""

    def __init__(self, log_dir: Path | None = None) -> None:
        self.log_dir = log_dir or JIUWENBOX_HOME / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # A single worker keeps writes ordered without forcing the event loop
        # to wait on disk I/O. ``thread_name_prefix`` aids in debugging.
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="jiuwenbox-audit",
        )
        self._lock = threading.Lock()

    def _serialize_event(self, event: AuditEvent) -> tuple[Path, str]:
        log_file = self.log_dir / f"{event.sandbox_id}.log"
        line = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        return log_file, line

    def log_event(self, event: AuditEvent) -> None:
        """Schedule a non-blocking append of ``event`` to its sandbox log.

        The serialization happens synchronously on the caller's thread (it is
        cheap for the small audit payloads jiuwenbox produces) but the file
        write is dispatched to a dedicated background thread so callers in
        the asyncio event loop are not blocked on disk I/O.
        """
        log_file, line = self._serialize_event(event)
        with self._lock:
            self._executor.submit(_write_jsonl_line, log_file, line)
        logger.debug("Audit: %s %s", event.event_type.value, event.sandbox_id)

    def log_event_sync(self, event: AuditEvent) -> None:
        """Synchronous append used by tests and shutdown paths."""
        log_file, line = self._serialize_event(event)
        _write_jsonl_line(log_file, line)
        logger.debug("Audit (sync): %s %s", event.event_type.value, event.sandbox_id)

    def log(
        self,
        event_type: AuditEventType,
        sandbox_id: str,
        **details: object,
    ) -> None:
        """Convenience helper to create and log an event."""
        event = AuditEvent(
            event_type=event_type,
            sandbox_id=sandbox_id,
            details=details,
        )
        self.log_event(event)

    def flush(self, timeout: float | None = None) -> None:
        """Wait for queued audit writes to drain, primarily for tests."""
        with self._lock:
            executor = self._executor
        # Use an empty submit() and wait so any prior writes complete.
        future = executor.submit(lambda: None)
        future.result(timeout=timeout)

    def read_logs(self, sandbox_id: str) -> list[AuditEvent]:
        """Read all audit events for a sandbox."""
        self.flush()
        log_file = self.log_dir / f"{sandbox_id}.log"
        if not log_file.exists():
            return []
        events: list[AuditEvent] = []
        for line in log_file.read_text().splitlines():
            if line.strip():
                events.append(AuditEvent.model_validate_json(line))
        return events

    def read_logs_raw(self, sandbox_id: str) -> str:
        """Read raw log text for a sandbox."""
        self.flush()
        log_file = self.log_dir / f"{sandbox_id}.log"
        if not log_file.exists():
            return ""
        return log_file.read_text()

    def delete_logs(self, sandbox_id: str) -> None:
        """Delete logs for a sandbox."""
        self.flush()
        log_file = self.log_dir / f"{sandbox_id}.log"
        log_file.unlink(missing_ok=True)
