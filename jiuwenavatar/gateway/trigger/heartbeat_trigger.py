# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Heartbeat Trigger — 心跳触发器，复用现有 Heartbeat 机制."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from jiuwenavatar.gateway.trigger.base import ITrigger, TriggerCallback
from jiuwenavatar.gateway.trigger.models import TriggerConfig

logger = logging.getLogger(__name__)


class HeartbeatTrigger(ITrigger):
    """Heartbeat-based trigger that fires at regular intervals.

    Similar to the existing GatewayHeartbeatService but integrated
    into the Trigger Engine framework.
    """

    def __init__(self, config: TriggerConfig, callback: TriggerCallback) -> None:
        super().__init__(config, callback)
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        interval = self._config.interval_seconds
        if not interval or interval <= 0:
            logger.error("HeartbeatTrigger %s: invalid interval_seconds", self.trigger_id)
            return

        self._running = True
        self._task = asyncio.create_task(
            self._run_loop(), name=f"heartbeat-trigger-{self.trigger_id}"
        )
        logger.info("HeartbeatTrigger %s started with interval %.0fs", self.trigger_id, interval)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("HeartbeatTrigger %s stopped", self.trigger_id)

    def is_running(self) -> bool:
        return self._running

    async def _run_loop(self) -> None:
        """Main loop: sleep for interval, check active_hours, then fire."""
        interval = self._config.interval_seconds
        assert interval and interval > 0

        while self._running:
            try:
                # Sleep in chunks to allow cancellation
                sleep_remaining = interval
                while sleep_remaining > 0 and self._running:
                    chunk = min(sleep_remaining, 60.0)
                    await asyncio.sleep(chunk)
                    sleep_remaining -= chunk

                if not self._running:
                    break

                # Check active hours
                if not self._is_within_active_hours():
                    continue

                # Fire!
                await self.fire()
                logger.debug("HeartbeatTrigger %s fired", self.trigger_id)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("HeartbeatTrigger %s error", self.trigger_id)
                await asyncio.sleep(30)

    def _is_within_active_hours(self) -> bool:
        """Check if current time is within configured active hours."""
        from jiuwenavatar.gateway.heartbeat.heartbeat import normalize_active_hours

        active_hours = self._config.active_hours
        if not active_hours:
            return True  # No restriction

        normalized = normalize_active_hours(active_hours)
        if not normalized:
            return True

        import datetime as _dt

        now = _dt.datetime.now()
        current_minutes = now.hour * 60 + now.minute

        start_str = normalized.get("start", "00:00")
        end_str = normalized.get("end", "23:59")

        try:
            start_h, start_m = map(int, start_str.split(":"))
            end_h, end_m = map(int, end_str.split(":"))
            start_minutes = start_h * 60 + start_m
            end_minutes = end_h * 60 + end_m

            if start_minutes <= end_minutes:
                return start_minutes <= current_minutes <= end_minutes
            else:
                # Crosses midnight
                return current_minutes >= start_minutes or current_minutes <= end_minutes
        except (ValueError, AttributeError):
            return True
