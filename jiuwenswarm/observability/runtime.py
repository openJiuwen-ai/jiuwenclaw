# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AgentServer lifecycle for the Core OTLP record consumer."""

from __future__ import annotations

import logging
import threading
from typing import Protocol

from jiuwenswarm.observability.config import (
    TrajectoryStoreSettings,
    load_trajectory_store_settings,
)
from jiuwenswarm.observability.sink import CommitCallback, TrajectoryRecordSink
from jiuwenswarm.observability.updates import trajectory_update_broker

logger = logging.getLogger(__name__)


class _SpanRecordProcessorLike(Protocol):
    def register_consumer(self, consumer: TrajectoryRecordSink) -> None:
        """Register one sink by object identity."""
        ...

    def unregister_consumer(self, consumer: TrajectoryRecordSink) -> None:
        """Unregister one sink by object identity."""
        ...


_runtime_lock = threading.RLock()
_runtime_sink: TrajectoryRecordSink | None = None
_runtime_processor: _SpanRecordProcessorLike | None = None
_runtime_settings: TrajectoryStoreSettings | None = None


def sync_trajectory_runtime(
    settings: TrajectoryStoreSettings | None = None,
    *,
    on_commit: CommitCallback | None = None,
) -> TrajectoryRecordSink | None:
    """Synchronize the AgentServer sink with the current trajectory settings."""
    resolved = settings or load_trajectory_store_settings()
    if not resolved.enabled:
        shutdown_trajectory_runtime()
        return None
    return start_trajectory_runtime(resolved, on_commit=on_commit)


def start_trajectory_runtime(
    settings: TrajectoryStoreSettings | None = None,
    *,
    on_commit: CommitCallback | None = None,
) -> TrajectoryRecordSink | None:
    """Start and register the process-wide sink once.

    The sink is ready before it is exposed to Core. A settings change performs
    the inverse lifecycle first: unregister, drain, close, then replace.
    """
    global _runtime_processor, _runtime_settings, _runtime_sink

    resolved = settings or load_trajectory_store_settings()
    if not resolved.enabled:
        shutdown_trajectory_runtime()
        return None
    with _runtime_lock:
        if (
            _runtime_sink is not None
            and _runtime_processor is not None
            and _runtime_settings == resolved
        ):
            _runtime_sink.set_commit_callback(_combined_commit_callback(on_commit))
            return _runtime_sink
        if _runtime_sink is not None or _runtime_processor is not None:
            if not _shutdown_locked():
                raise RuntimeError("Previous trajectory runtime did not stop cleanly")

        sink = _create_sink(
            resolved,
            on_commit=_combined_commit_callback(on_commit),
        )
        try:
            sink.start()
            processor = _get_core_span_record_processor()
            processor.register_consumer(sink)
        except Exception:
            try:
                sink.close()
            except Exception:
                logger.exception("Trajectory runtime startup cleanup failed")
            raise
        _runtime_sink = sink
        _runtime_processor = processor
        _runtime_settings = resolved
        logger.info(
            "Trajectory runtime enabled: database=%s queue_size=%d",
            resolved.database_path,
            resolved.queue_size,
        )
        return sink


def shutdown_trajectory_runtime(*, timeout: float = 15.0) -> bool:
    """Unregister first, then drain and close the process-wide sink."""
    with _runtime_lock:
        return _shutdown_locked(timeout=timeout)


def get_trajectory_runtime_sink() -> TrajectoryRecordSink | None:
    """Return the active process-local sink for diagnostics."""
    with _runtime_lock:
        return _runtime_sink


def _shutdown_locked(*, timeout: float = 15.0) -> bool:
    global _runtime_processor, _runtime_settings, _runtime_sink

    sink = _runtime_sink
    processor = _runtime_processor
    if sink is None and processor is None:
        _runtime_settings = None
        return True

    unregistered = processor is None
    if processor is not None and sink is not None:
        try:
            processor.unregister_consumer(sink)
        except Exception:
            logger.exception("Trajectory runtime consumer unregister failed")
            # Keep the still-running sink reachable and registered. Closing it
            # here would leave a stopped singleton behind while Core may still
            # retain the consumer reference.
            return False
        else:
            _runtime_processor = None
            unregistered = True

    stopped = sink is None
    if sink is not None:
        try:
            stopped = sink.close(timeout=timeout)
        except Exception:
            logger.exception("Trajectory runtime sink shutdown failed")
            stopped = False

    if unregistered and stopped:
        _runtime_sink = None
        _runtime_processor = None
        _runtime_settings = None
        logger.info("Trajectory runtime disabled")
        return True
    return False


def _create_sink(
    settings: TrajectoryStoreSettings,
    *,
    on_commit: CommitCallback | None,
) -> TrajectoryRecordSink:
    return TrajectoryRecordSink(settings, on_commit=on_commit)


def _combined_commit_callback(on_commit: CommitCallback | None) -> CommitCallback:
    def _publish(updates) -> None:
        trajectory_update_broker.publish(updates)
        if on_commit is not None:
            on_commit(updates)

    return _publish


def _get_core_span_record_processor() -> _SpanRecordProcessorLike:
    from openjiuwen.extensions.observability.demand import get_span_record_processor

    return get_span_record_processor()


__all__ = [
    "get_trajectory_runtime_sink",
    "shutdown_trajectory_runtime",
    "start_trajectory_runtime",
    "sync_trajectory_runtime",
]
