# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cron Trigger — 定时触发器（基于 croniter 的自包含轻量调度器）。

注意：本实现**不依赖** gateway/cron 下的 CronController/CronSchedulerService，
而是自带一个独立的调度循环直接回调 TriggerEngine，避免与旧 cron 子系统耦合。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import croniter

from jiuwenavatar.gateway.trigger.base import ITrigger, TriggerCallback
from jiuwenavatar.gateway.trigger.models import TriggerConfig

logger = logging.getLogger(__name__)


class CronTrigger(ITrigger):
    """Cron-based trigger that fires at scheduled times.

    Instead of directly using CronController (which pushes through the
    Gateway message pipeline), this implements its own lightweight scheduler
    that calls the trigger callback directly — keeping the trigger engine
    self-contained while existing CronController continues to work for
    backward compatibility.
    """

    def __init__(self, config: TriggerConfig, callback: TriggerCallback) -> None:
        super().__init__(config, callback)
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        cron_expr = self._config.cron_expr
        if not cron_expr:
            logger.error("CronTrigger %s: no cron_expr configured", self.trigger_id)
            return

        try:
            # Validate cron expression
            croniter.croniter(cron_expr)
        except (ValueError, KeyError) as e:
            logger.error("CronTrigger %s: invalid cron_expr '%s': %s", self.trigger_id, cron_expr, e)
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name=f"cron-trigger-{self.trigger_id}")
        logger.info("CronTrigger %s started with expr '%s'", self.trigger_id, cron_expr)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("CronTrigger %s stopped", self.trigger_id)

    def is_running(self) -> bool:
        return self._running

    async def _run_loop(self) -> None:
        """Main scheduling loop — sleep until next fire time, then fire."""
        import zoneinfo

        cron_expr = self._config.cron_expr
        assert cron_expr is not None
        tz_name = self._config.timezone or "Asia/Shanghai"

        try:
            tz = zoneinfo.ZoneInfo(tz_name)
        except zoneinfo.ZoneInfoNotFoundError:
            logger.warning("CronTrigger %s: unknown timezone '%s', using UTC", self.trigger_id, tz_name)
            tz = timezone.utc

        while self._running:
            try:
                now = datetime.now(tz=tz)
                cron = croniter.croniter(cron_expr, now)
                next_dt = cron.get_next(datetime)

                # Calculate seconds until next fire
                delta = (next_dt - now).total_seconds()
                if delta <= 0:
                    # Skip to next if we're already past
                    delta = 1.0

                # Sleep in chunks to allow cancellation
                sleep_remaining = delta
                while sleep_remaining > 0 and self._running:
                    chunk = min(sleep_remaining, 60.0)  # Check every 60s max
                    await asyncio.sleep(chunk)
                    sleep_remaining -= chunk

                if not self._running:
                    break

                # Fire!
                await self.fire()
                logger.info("CronTrigger %s fired at %s", self.trigger_id, datetime.now(tz=tz).isoformat())

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("CronTrigger %s error in run loop", self.trigger_id)
                await asyncio.sleep(30)  # Back off on error
