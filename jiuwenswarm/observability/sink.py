# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bounded AgentServer consumer and SQLite writer for Core OTLP records."""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable

from jiuwenswarm.observability.config import TrajectoryStoreSettings
from jiuwenswarm.observability.models import (
    CommittedTraceUpdate,
    OtlpSpanRecordLike,
    OtlpSpanSnapshotRecordLike,
    TraceRecordData,
    TraceSinkStats,
)
from jiuwenswarm.observability.store import TrajectoryStore

logger = logging.getLogger(__name__)

CommitCallback = Callable[[tuple[CommittedTraceUpdate, ...]], None]
_RETENTION_INTERVAL_SECONDS = 3600
# One retry keeps the two five-second SQLite busy waits below the default
# fifteen-second shutdown deadline, including the retry delay.
_WRITE_RETRY_DELAYS_SECONDS = (0.05,)


class TrajectoryRecordSink:
    """Fast Core consumer backed by a bounded queue and one writer thread."""

    def __init__(
        self,
        settings: TrajectoryStoreSettings,
        *,
        on_commit: CommitCallback | None = None,
        store: TrajectoryStore | None = None,
    ) -> None:
        self.settings = settings
        self._on_commit = on_commit
        self._store = store or TrajectoryStore(
            settings.database_path,
            retention_days=settings.retention_days,
        )
        self._queue: queue.Queue[OtlpSpanRecordLike] = queue.Queue(
            maxsize=settings.queue_size,
        )
        self._snapshot_pending: OrderedDict[
            tuple[str, str], OtlpSpanSnapshotRecordLike
        ] = OrderedDict()
        self._work_available = threading.Event()
        self._state_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._startup_error: BaseException | None = None
        self._accepting = False
        self._accepted = 0
        self._committed = 0
        self._dropped = 0
        self._failed = 0
        self._conflicts = 0
        self._coalesced = 0
        self._evicted_provisional = 0
        self._dropped_final = 0
        self._stale_ignored = 0

    def start(self, *, timeout: float = 10.0) -> None:
        """Initialize SQLite on the writer thread and enable fast consumption.

        Args:
            timeout: Maximum seconds to wait for schema initialization.

        Raises:
            RuntimeError: If the writer cannot initialize or times out.
        """
        with self._state_lock:
            if self._thread is not None:
                if self._startup_error is not None:
                    raise RuntimeError("Trajectory writer failed to initialize") from self._startup_error
                return
            self._startup_error = None
            self._stop_requested.clear()
            self._ready.clear()
            thread = threading.Thread(
                target=self._writer_main,
                name="trajectory-record-writer",
                daemon=True,
            )
            self._thread = thread
            thread.start()
        if not self._ready.wait(timeout=max(0.1, float(timeout))):
            self._stop_requested.set()
            raise RuntimeError("Trajectory writer initialization timed out")
        if self._startup_error is not None:
            raise RuntimeError("Trajectory writer failed to initialize") from self._startup_error
        with self._state_lock:
            if (
                self._thread is not thread
                or self._stop_requested.is_set()
                or not thread.is_alive()
            ):
                raise RuntimeError("Trajectory writer stopped during initialization")
            self._accepting = True

    def consume(self, record: OtlpSpanRecordLike) -> None:
        """Atomically accept one frozen Core record without storage work.

        Core's processor publishes an immutable record whose ``raw_json`` is
        already ``bytes``. Validation, copying, hashing, and SQLite work stay
        on the writer thread so a full queue remains a constant-cost decision.
        """
        if not isinstance(getattr(record, "raw_json", None), bytes):
            self._increment("failed")
            logger.warning("Trajectory record rejected before queueing: raw_json must be bytes")
            return
        with self._state_lock:
            if not self._accepting:
                self._increment("dropped")
                logger.warning("Trajectory record dropped because the sink is not accepting records")
                return
            identity = (
                str(getattr(record, "trace_id", "") or "").strip().lower(),
                str(getattr(record, "span_id", "") or "").strip().lower(),
            )
            if identity in self._snapshot_pending:
                self._snapshot_pending.pop(identity, None)
                self._increment("coalesced")
            try:
                # Keep this non-blocking enqueue under the same lock used by
                # close(): every accepted record is therefore visible before
                # the writer receives its stop request.
                self._queue.put_nowait(record)
            except queue.Full:
                self._increment("dropped")
                self._increment("dropped_final")
                logger.warning(
                    "Trajectory queue is full; SQLite fan-out dropped the record "
                    "while other configured exporters remain unaffected: "
                    "trace_id=%s span_id=%s",
                    getattr(record, "trace_id", None),
                    getattr(record, "span_id", None),
                )
                return
            self._work_available.set()
        self._increment("accepted")

    def consume_snapshot(self, record: OtlpSpanSnapshotRecordLike) -> None:
        """Accept a live snapshot using identity-keyed latest-wins coalescing."""
        if not isinstance(getattr(record, "raw_json", None), bytes):
            self._increment("failed")
            logger.warning("Trajectory snapshot rejected before queueing: raw_json must be bytes")
            return
        identity = (
            str(getattr(record, "trace_id", "") or "").strip().lower(),
            str(getattr(record, "span_id", "") or "").strip().lower(),
        )
        try:
            revision = int(getattr(record, "record_revision"))
        except (TypeError, ValueError, OverflowError):
            self._increment("failed")
            logger.warning("Trajectory snapshot rejected before queueing: invalid record_revision")
            return
        if not identity[0] or not identity[1] or revision < 1:
            self._increment("failed")
            logger.warning("Trajectory snapshot rejected before queueing: invalid identity or revision")
            return
        with self._state_lock:
            if not self._accepting:
                self._increment("dropped")
                return
            current = self._snapshot_pending.get(identity)
            if current is not None:
                current_revision = int(getattr(current, "record_revision", 0))
                if revision <= current_revision:
                    self._increment("stale_ignored")
                    return
                self._snapshot_pending[identity] = record
                self._snapshot_pending.move_to_end(identity)
                self._increment("coalesced")
                self._work_available.set()
                self._increment("accepted")
                return
            if self._queue.qsize() + len(self._snapshot_pending) >= self.settings.queue_size:
                if self._snapshot_pending:
                    self._snapshot_pending.popitem(last=False)
                    self._increment("evicted_provisional")
                    self._increment("dropped")
                else:
                    self._increment("dropped")
                    return
            self._snapshot_pending[identity] = record
            self._work_available.set()
        self._increment("accepted")

    def close(self, *, timeout: float = 15.0) -> bool:
        """Stop accepting, drain queued records, and close SQLite.

        Args:
            timeout: Maximum seconds to wait for the writer thread.

        Returns:
            ``True`` when the writer drained and stopped before the timeout.
        """
        with self._state_lock:
            self._accepting = False
            thread = self._thread
        if thread is None:
            return True
        self._stop_requested.set()
        self._work_available.set()
        thread.join(timeout=max(0.1, float(timeout)))
        stopped = not thread.is_alive()
        if not stopped:
            logger.warning(
                "Trajectory writer shutdown timed out with %d queued records",
                self._queue.qsize(),
            )
            return False
        with self._state_lock:
            self._thread = None
        return True

    def stats(self) -> TraceSinkStats:
        """Return an atomic snapshot of sink counters."""
        with self._stats_lock:
            return TraceSinkStats(
                accepted=self._accepted,
                committed=self._committed,
                dropped=self._dropped,
                failed=self._failed,
                conflicts=self._conflicts,
                queued=self._queue.qsize() + len(self._snapshot_pending),
                coalesced=self._coalesced,
                evicted_provisional=self._evicted_provisional,
                dropped_final=self._dropped_final,
                stale_ignored=self._stale_ignored,
            )

    def set_commit_callback(self, on_commit: CommitCallback | None) -> None:
        """Replace the post-commit notification callback atomically."""
        with self._state_lock:
            self._on_commit = on_commit

    def _writer_main(self) -> None:
        try:
            self._store.initialize()
            try:
                self._store.delete_expired()
            except Exception:
                logger.exception("Initial trajectory retention cleanup failed")
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            logger.exception("Trajectory writer initialization failed")
            return
        self._ready.set()
        next_retention_at = time.monotonic() + _RETENTION_INTERVAL_SECONDS
        flush_timeout = self.settings.flush_interval_ms / 1000
        try:
            while (
                not self._stop_requested.is_set()
                or not self._queue.empty()
                or bool(self._snapshot_pending)
            ):
                batch = self._take_batch(flush_timeout)
                if batch:
                    self._write_batch(batch)
                if time.monotonic() >= next_retention_at:
                    try:
                        self._store.delete_expired()
                    except Exception:
                        logger.exception("Trajectory retention cleanup failed")
                    next_retention_at = time.monotonic() + _RETENTION_INTERVAL_SECONDS
        finally:
            self._store.close()

    def _take_batch(
        self,
        timeout: float,
    ) -> list[tuple[OtlpSpanRecordLike | OtlpSpanSnapshotRecordLike, bool]]:
        self._work_available.wait(timeout=max(0.001, timeout))
        self._work_available.clear()
        batch: list[tuple[OtlpSpanRecordLike | OtlpSpanSnapshotRecordLike, bool]] = []
        while len(batch) < self.settings.batch_size:
            try:
                batch.append((self._queue.get_nowait(), True))
            except queue.Empty:
                break
        with self._state_lock:
            while len(batch) < self.settings.batch_size and self._snapshot_pending:
                _identity, snapshot = self._snapshot_pending.popitem(last=False)
                batch.append((snapshot, False))
            if not self._queue.empty() or self._snapshot_pending:
                self._work_available.set()
        return batch

    def _write_batch(
        self,
        batch: list[tuple[OtlpSpanRecordLike | OtlpSpanSnapshotRecordLike, bool]],
    ) -> None:
        try:
            records: list[TraceRecordData] = []
            for record, queued_final in batch:
                try:
                    if queued_final:
                        records.append(TraceRecordData.from_core_record(record))
                    else:
                        records.append(TraceRecordData.from_core_snapshot(record))
                except (AttributeError, TypeError, ValueError, OverflowError) as exc:
                    self._increment("failed")
                    logger.warning("Trajectory record rejected by writer: %s", exc)
            if not records:
                return
            result = self._write_records_with_retry(records)
        except Exception:
            self._increment("failed", len(records))
            logger.exception(
                "Trajectory batch commit failed; records remain available only through existing exporters"
            )
        else:
            self._increment("committed", result.inserted)
            self._increment("conflicts", result.conflicts)
            with self._state_lock:
                on_commit = self._on_commit
            if result.updates and on_commit is not None:
                try:
                    on_commit(result.updates)
                except Exception:
                    logger.exception("Trajectory commit notification callback failed")
        finally:
            for _record, queued_final in batch:
                if queued_final:
                    self._queue.task_done()

    def _write_records_with_retry(
        self,
        records: list[TraceRecordData],
    ):
        """Retry transient SQLite contention without creating an infinite drain."""
        for attempt, delay in enumerate((*_WRITE_RETRY_DELAYS_SECONDS, None)):
            try:
                return self._store.write_records(records)
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                retryable = "locked" in message or "busy" in message
                if not retryable or delay is None:
                    raise
                logger.warning(
                    "Trajectory SQLite commit contention; retrying batch: attempt=%d",
                    attempt + 1,
                )
                time.sleep(delay)
        raise AssertionError("unreachable trajectory write retry state")

    def _increment(self, counter: str, amount: int = 1) -> None:
        with self._stats_lock:
            if counter == "accepted":
                self._accepted += amount
            elif counter == "committed":
                self._committed += amount
            elif counter == "dropped":
                self._dropped += amount
            elif counter == "failed":
                self._failed += amount
            elif counter == "conflicts":
                self._conflicts += amount
            elif counter == "coalesced":
                self._coalesced += amount
            elif counter == "evicted_provisional":
                self._evicted_provisional += amount
            elif counter == "dropped_final":
                self._dropped_final += amount
            elif counter == "stale_ignored":
                self._stale_ignored += amount
            else:
                raise ValueError(f"unknown trajectory counter: {counter}")


__all__ = ["CommitCallback", "TrajectoryRecordSink"]
