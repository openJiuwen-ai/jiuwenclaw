# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trace samplers — select a batch of trace_ids from traces.db."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from jiuwenswarm.evolve.models import TraceBatch
from jiuwenswarm.evolve.registry import trace_samplers

logger = logging.getLogger(__name__)


class TraceSampler(ABC):
    """Select a :class:`TraceBatch` from the trace database."""

    def __init__(self, name: str, trace_reader: object) -> None:
        self.name = name
        self._reader = trace_reader

    @abstractmethod
    def sample(self) -> TraceBatch:
        """Return a TraceBatch ready for the pipeline."""
        ...


@trace_samplers.register("latest_n")
class LatestNSampler(TraceSampler):
    """Sample the most recent N distinct trace_ids."""

    def __init__(
        self,
        trace_reader: object,
        max_traces: int = 20,
        source: str = "periodic",
    ) -> None:
        super().__init__(name="latest_n", trace_reader=trace_reader)
        self._max = max_traces
        self._source = source

    def sample(self) -> TraceBatch:
        trace_ids = self._reader.get_recent_trace_ids(limit=self._max)
        logger.info(
            "LatestNSampler: sampled %d traces (max=%d, source=%s)",
            len(trace_ids), self._max, self._source,
        )
        return TraceBatch(
            trace_ids=trace_ids,
            source=self._source,
        )


@trace_samplers.register("time_window")
class TimeWindowSampler(TraceSampler):
    """Sample traces within a time window."""

    def __init__(
        self,
        trace_reader: object,
        since: str | None = None,
        limit: int = 100,
        source: str = "periodic",
    ) -> None:
        super().__init__(name="time_window", trace_reader=trace_reader)
        self._since = since
        self._limit = limit
        self._source = source

    def sample(self) -> TraceBatch:
        if self._since:
            trace_ids = self._reader.get_trace_ids_since(
                self._since, limit=self._limit
            )
        else:
            # Fallback: last 24 hours
            from datetime import datetime, timezone, timedelta

            since = (
                datetime.now(timezone.utc) - timedelta(hours=24)
            ).isoformat()
            trace_ids = self._reader.get_trace_ids_since(
                since, limit=self._limit
            )
        logger.info(
            "TimeWindowSampler: sampled %d traces (since=%s)",
            len(trace_ids), self._since or "24h ago",
        )
        return TraceBatch(
            trace_ids=trace_ids,
            source=self._source,
        )
