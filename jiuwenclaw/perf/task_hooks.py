# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Hooks for TaskExecutionRail → RequestSummary task attribution."""

from __future__ import annotations

import logging

from jiuwenclaw.perf.collector import get_perf_collector
from jiuwenclaw.perf.config import get_perf_summary_config
from jiuwenclaw.perf.context import get_request_context
from jiuwenclaw.perf.events import TaskPerfEvent

logger = logging.getLogger(__name__)


def notify_task_complete(
    *,
    task_id: str,
    task_content: str,
    source: str,
    started_at: float,
    ended_at: float,
    duration_ms: float,
    status: str,
    request_id: str | None = None,
) -> None:
    """Record a completed todo task into the request summary accumulator."""
    if not get_perf_summary_config().enabled:
        return

    rid = (request_id or "").strip()
    if not rid:
        req_ctx = get_request_context()
        if req_ctx is None:
            logger.debug(
                "[perf] skip task.complete record: no request context task_id=%s",
                task_id,
            )
            return
        rid = str(req_ctx.get("request_id") or "").strip()
    if not rid:
        logger.debug(
            "[perf] skip task.complete record: no request_id task_id=%s",
            task_id,
        )
        return

    get_perf_collector().record_task(
        rid,
        TaskPerfEvent(
            task_id=task_id,
            task_content=task_content,
            source=source,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=float(duration_ms),
            status=status,
        ),
    )
