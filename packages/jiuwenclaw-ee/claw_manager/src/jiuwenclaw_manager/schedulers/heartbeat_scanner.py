"""定时巡检：将超时未上报心跳的 online 实例标记为 offline。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.infrastructure.config import settings
from jiuwenclaw_manager.infrastructure.utils import utc_now
from jiuwenclaw_manager.infrastructure.logger import get_logger
from jiuwenclaw_manager.core.instance.instance_service import mark_instance_offline
from jiuwenclaw_manager.models.instance_models import INSTANCE_INFO_TABLE_DEF

_log = get_logger(__name__)
_TABLE = INSTANCE_INFO_TABLE_DEF.table_name


async def run_heartbeat_scan_loop(stop: asyncio.Event, handler: DBHandler) -> None:
    interval = max(60, settings.MANAGER_HEARTBEAT_SCAN_INTERVAL_SECONDS)
    timeout_sec = max(120, settings.manager_heartbeat_timeout_seconds)
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
            for row in rows:
                last_hb = getattr(row, "last_heartbeat", None)
                if last_hb is None:
                    continue
                hb = last_hb
                if hb.tzinfo is None:
                    hb = hb.replace(tzinfo=timezone.utc)
                if hb >= cutoff:
                    continue
                jid = str(getattr(row, "jiuwenclaw_id", "") or "").strip()
                if jid:
                    await mark_instance_offline(handler, jid)
                    marked += 1
            if marked:
                _log.info("heartbeat_scan_marked_offline", count=marked)
        except Exception as exc:  # noqa: BLE001
            _log.warning("heartbeat_scan_failed", error=str(exc))
