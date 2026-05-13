"""定时巡检：将超时未上报心跳的 ONLINE 服务标记为 offline（设计文档 5.4）。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from jiuwenclaw_manager.config import settings
from jiuwenclaw_manager.infrastructure.db import get_session_factory
from jiuwenclaw_manager.infrastructure.logger import get_logger
from jiuwenclaw_manager.models.db.instance import ServiceInstance

_log = get_logger(__name__)


async def run_heartbeat_scan_loop(stop: asyncio.Event) -> None:
    interval = max(5, settings.scan_interval_seconds)
    timeout_sec = max(5, settings.heartbeat_timeout_seconds)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass
        try:
            factory = get_session_factory()
            async with factory() as session:
                cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_sec)
                stmt = (
                    update(ServiceInstance)
                    .where(
                        ServiceInstance.status == "online",
                        ServiceInstance.last_heartbeat.is_not(None),
                        ServiceInstance.last_heartbeat < cutoff,
                    )
                    .values(status="offline")
                )
                result = await session.execute(stmt)
                await session.commit()
                if result.rowcount:
                    _log.info("heartbeat_scan_marked_offline", count=result.rowcount)
        except Exception as exc:  # noqa: BLE001
            _log.warning("heartbeat_scan_failed", error=str(exc))
