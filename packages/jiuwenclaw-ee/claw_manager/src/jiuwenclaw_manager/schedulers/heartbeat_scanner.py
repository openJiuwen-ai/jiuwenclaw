"""定时巡检：将超时未上报心跳的 ONLINE 服务标记为 offline（设计文档 5.4）。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.config import settings
from jiuwenclaw_manager.core.utils import utc_now
from jiuwenclaw_manager.infrastructure.logger import get_logger
from jiuwenclaw_manager.models.table_defs.instance_models import SERVICE_INSTANCE_TABLE_DEF

_log = get_logger(__name__)
_TABLE = SERVICE_INSTANCE_TABLE_DEF.table_name


async def run_heartbeat_scan_loop(stop: asyncio.Event, handler: DBHandler) -> None:
    interval = max(5, settings.scan_interval_seconds)
    timeout_sec = max(5, settings.heartbeat_timeout_seconds)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_sec)
            rows = await handler.list_records(
                _TABLE, {"status": "online"}, limit=10_000, offset=0
            )
            marked = 0
            now = utc_now()
            for row in rows:
                last_hb = getattr(row, "last_heartbeat", None)
                if last_hb is None:
                    continue
                hb = last_hb
                if hb.tzinfo is None:
                    hb = hb.replace(tzinfo=timezone.utc)
                if hb >= cutoff:
                    continue
                row_id = int(getattr(row, "id"))
                updated = await handler.update(
                    _TABLE,
                    {"id": row_id},
                    {"status": "offline", "updated_at": now},
                )
                if updated is not None:
                    marked += 1
            if marked:
                _log.info("heartbeat_scan_marked_offline", count=marked)
        except Exception as exc:  # noqa: BLE001
            _log.warning("heartbeat_scan_failed", error=str(exc))
