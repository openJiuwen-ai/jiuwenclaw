# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cron-based scheduling for Dreaming memory consolidation."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone as datetime_timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from croniter import croniter

logger = logging.getLogger(__name__)


def _next_scheduled_time(cron_expr: str, base_time: datetime) -> datetime:
    """Return the next cron occurrence after an aware base time."""
    if base_time.tzinfo is None:
        raise ValueError("base_time must be timezone-aware")

    field_count = len(cron_expr.split())
    if field_count not in (5, 7):
        raise ValueError(
            f"cron_expr must have 5 or 7 fields, got {field_count}"
        )

    second_at_beginning = field_count == 7
    if not croniter.is_valid(
        cron_expr,
        second_at_beginning=second_at_beginning,
    ):
        raise ValueError(f"invalid cron expression: {cron_expr!r}")

    next_time = croniter(
        cron_expr,
        base_time,
        second_at_beginning=second_at_beginning,
    ).get_next(datetime)
    if not isinstance(next_time, datetime):
        raise RuntimeError("croniter returned an invalid datetime")
    if next_time.tzinfo is None:
        next_time = next_time.replace(tzinfo=base_time.tzinfo)
    return next_time


def _delay_until(next_time: datetime, now: datetime) -> float:
    """Return elapsed seconds until an occurrence, including DST changes."""
    if next_time.tzinfo is None or now.tzinfo is None:
        raise ValueError("scheduled times must be timezone-aware")
    return max(
        0.0,
        (
            next_time.astimezone(datetime_timezone.utc)
            - now.astimezone(datetime_timezone.utc)
        ).total_seconds(),
    )


class CronDreamingOrchestrator:
    """Idle-aware Dreaming service triggered by a cron expression."""

    def __init__(
        self,
        sweep_fn: Callable[[], Awaitable[None]],
        cron_expr: str,
        timezone: str,
        busy_checker: Callable[[], bool] | None = None,
        name: str = "dreaming",
    ) -> None:
        self._sweep_fn = sweep_fn
        self._cron_expr = cron_expr.strip()
        self._timezone_name = timezone.strip()
        self._timezone = ZoneInfo(self._timezone_name)
        self._busy_checker = busy_checker
        self._name = name
        self._task: asyncio.Task[None] | None = None
        self._running = False

        _next_scheduled_time(
            self._cron_expr,
            datetime.now(self._timezone),
        )

    @property
    def health(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "cron_expr": self._cron_expr,
            "timezone": self._timezone_name,
        }

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(
            self._loop(),
            name=f"{self._name}-loop",
        )
        logger.info(
            "[%s] Orchestrator started, cron=%s timezone=%s",
            self._name,
            self._cron_expr,
            self._timezone_name,
        )

    async def stop(self) -> None:
        if not self._running and self._task is None:
            return
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("[%s] Orchestrator stopped", self._name)

    async def _loop(self) -> None:
        try:
            while self._running:
                now = datetime.now(self._timezone)
                next_time = _next_scheduled_time(
                    self._cron_expr,
                    now,
                )
                delay = _delay_until(next_time, now)
                logger.info(
                    "[%s] next sweep scheduled at %s",
                    self._name,
                    next_time.isoformat(),
                )
                await asyncio.sleep(delay)
                if self._running:
                    await self._tick()
        except Exception:
            logger.exception(
                "[%s] cron loop terminated unexpectedly",
                self._name,
            )
            self._running = False

    async def _tick(self) -> None:
        try:
            if self._busy_checker is not None:
                try:
                    if self._busy_checker():
                        logger.info(
                            "[%s] agent busy, skip scheduled sweep",
                            self._name,
                        )
                        return
                except Exception:
                    logger.warning(
                        "[%s] busy_checker raised an exception, "
                        "skipping the check",
                        self._name,
                        exc_info=True,
                    )

            logger.info("[%s] start sweep", self._name)
            await self._sweep_fn()
            logger.info("[%s] sweep completed", self._name)
        except Exception:
            logger.exception("[%s] sweep failed", self._name)
