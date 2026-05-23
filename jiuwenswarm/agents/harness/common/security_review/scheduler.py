# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded security review scheduling."""
from __future__ import annotations

from collections import Counter, deque

from jiuwenswarm.agents.harness.common.security_review.schema import (
    ReviewRequest,
    SecurityReviewConfig,
    Severity,
)

_RANK = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class SecurityReviewScheduler:
    """Deduplicates and bounds review requests."""

    def __init__(self, config: SecurityReviewConfig) -> None:
        self.config = config
        self._queue: deque[ReviewRequest] = deque()
        self._dedupe: set[tuple[str, ...]] = set()
        self._pending_sessions: dict[str, ReviewRequest] = {}
        self._last_scheduled_iteration: dict[str, int] = {}
        self._started_counts: Counter[str] = Counter()

    def schedule(self, request: ReviewRequest) -> bool:
        if request.dedupe_key in self._dedupe:
            return False
        if not self._passes_min_interval(request):
            return False
        existing_for_session = self._pending_sessions.get(request.session_id)
        if existing_for_session is not None:
            if _RANK[existing_for_session.priority] >= _RANK[request.priority]:
                return False
            self._remove_pending(existing_for_session)
        limit = max(1, self.config.async_queue_size)
        if len(self._queue) >= limit:
            if not self._replace_lower_priority(request):
                return False
        self._queue.append(request)
        self._dedupe.add(request.dedupe_key)
        self._pending_sessions[request.session_id] = request
        if request.iteration > 0:
            self._last_scheduled_iteration[request.session_id] = request.iteration
        return True

    def drain(self) -> list[ReviewRequest]:
        items = list(self._queue)
        self._queue.clear()
        self._dedupe.clear()
        self._pending_sessions.clear()
        return items

    def has_pending_work(self) -> bool:
        return bool(self._queue)

    def has_dedupe_key(self, dedupe_key: tuple[str, ...]) -> bool:
        return dedupe_key in self._dedupe

    def has_pending_session(self, session_id: str) -> bool:
        return session_id in self._pending_sessions

    def has_pending_timely_review(self, session_id: str) -> bool:
        request = self._pending_sessions.get(session_id)
        return (
            request is not None
            and request.request_type == "timely_tool_failure_review"
        )

    def can_defer_same_session_collision(self, request: ReviewRequest) -> bool:
        pending = self._pending_sessions.get(request.session_id)
        if pending is None:
            return False
        if (
            pending.request_type == "timely_tool_failure_review"
            and request.request_type == "session_end_review"
        ):
            return True
        return self._passes_min_interval(request)

    def record_deferred_request_accounting(self, request: ReviewRequest) -> None:
        if request.request_type == "timely_tool_failure_review":
            return
        if request.iteration > 0:
            self._last_scheduled_iteration[request.session_id] = request.iteration

    def drop_sessions(self, session_ids: set[str]) -> None:
        for request in list(self._queue):
            if request.session_id in session_ids:
                self._remove_pending(request)
        for session_id in session_ids:
            self._pending_sessions.pop(session_id, None)
            self._last_scheduled_iteration.pop(session_id, None)
            self._started_counts.pop(session_id, None)

    def mark_review_started(self, session_id: str) -> bool:
        if self._started_counts[session_id] >= max(1, self.config.max_reviews_per_session):
            return False
        self._started_counts[session_id] += 1
        return True

    def _replace_lower_priority(self, request: ReviewRequest) -> bool:
        incoming_rank = _RANK[request.priority]
        lower_priority_requests = [
            existing for existing in self._queue if _RANK[existing.priority] < incoming_rank
        ]
        if not lower_priority_requests:
            return False
        lowest_priority_request = min(
            lower_priority_requests,
            key=lambda existing: _RANK[existing.priority],
        )
        self._remove_pending(lowest_priority_request)
        return True

    def _remove_pending(self, request: ReviewRequest) -> None:
        try:
            self._queue.remove(request)
        except ValueError:
            pass
        self._dedupe.discard(request.dedupe_key)
        if self._pending_sessions.get(request.session_id) is request:
            self._pending_sessions.pop(request.session_id, None)

    def _passes_min_interval(self, request: ReviewRequest) -> bool:
        if request.request_type == "timely_tool_failure_review":
            return True
        if request.iteration <= 0:
            return True
        last_iteration = self._last_scheduled_iteration.get(request.session_id)
        if last_iteration is None:
            return True
        return (
            request.iteration - last_iteration
        ) >= max(1, self.config.min_review_interval_iterations)
