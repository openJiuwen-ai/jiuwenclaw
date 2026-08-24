# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""审计日志持久化 — JSONL 实时追加 + SQLite 结构化查询.

设计要点:
- JSONL 用于实时追加写入（零延迟，不阻塞 Hook 回调）
- SQLite 用于按维度高效查询（时间、类型、会话、渠道）
- 两者互为备份，JSONL 是原始数据，SQLite 是索引视图
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

from .models import Alert, AlertStatus, AuditEvent, AuditEventType

logger = logging.getLogger(__name__)

_JSONL_EVENTS_FILE = "audit_events.jsonl"
_JSONL_ALERTS_FILE = "audit_alerts.jsonl"
_DB_FILE = "audit.db"

_CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS audit_events (
    event_id      TEXT PRIMARY KEY,
    event_type    TEXT NOT NULL,
    timestamp     REAL NOT NULL,
    session_id    TEXT,
    channel_id    TEXT,
    request_id    TEXT,
    agent_name    TEXT,
    duration_ms   REAL,
    token_usage   TEXT,   -- JSON
    error_type    TEXT,
    error_detail  TEXT,
    metadata      TEXT    -- JSON
);
"""

_CREATE_ALERTS_TABLE = """
CREATE TABLE IF NOT EXISTS audit_alerts (
    alert_id      TEXT PRIMARY KEY,
    alert_type    TEXT NOT NULL,
    severity      TEXT NOT NULL,
    status        TEXT NOT NULL,
    triggered_at  REAL NOT NULL,
    rule_name     TEXT NOT NULL,
    message       TEXT,
    context       TEXT,    -- JSON
    resolved_at   REAL
);
"""

_CREATE_EVENTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON audit_events(timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_events_event_type ON audit_events(event_type);",
    "CREATE INDEX IF NOT EXISTS idx_events_session_id ON audit_events(session_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_channel_id ON audit_events(channel_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_request_id ON audit_events(request_id);",
    "CREATE INDEX IF NOT EXISTS idx_events_session_time ON audit_events(session_id, timestamp);",
]

_CREATE_ALERTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_alerts_triggered_at ON audit_alerts(triggered_at);",
    "CREATE INDEX IF NOT EXISTS idx_alerts_status ON audit_alerts(status);",
    "CREATE INDEX IF NOT EXISTS idx_alerts_severity ON audit_alerts(severity);",
    "CREATE INDEX IF NOT EXISTS idx_alerts_rule_status ON audit_alerts(rule_name, status);",
]


class LogStore:
    """审计日志持久化存储.

    用法::

        store = LogStore(Path("/path/to/audit"))
        await store.initialize()
        await store.write_event(event)
        events = await store.query_events({"event_type": "chat_error", "hours": 24})
    """

    def __init__(self, audit_dir: Path) -> None:
        self._audit_dir = audit_dir
        self._events_jsonl = audit_dir / _JSONL_EVENTS_FILE
        self._alerts_jsonl = audit_dir / _JSONL_ALERTS_FILE
        self._db_path = audit_dir / _DB_FILE
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._initialized = False

    @property
    def audit_dir(self) -> Path:
        return self._audit_dir

    @property
    def initialized(self) -> bool:
        return self._initialized and self._db is not None

    async def __aenter__(self) -> LogStore:
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()

    async def initialize(self) -> None:
        """创建目录、建表、建索引."""
        async with self._lifecycle_lock:
            if self.initialized:
                return

            self._audit_dir.mkdir(parents=True, exist_ok=True)
            db = await aiosqlite.connect(str(self._db_path))
            try:
                await db.execute("PRAGMA journal_mode=WAL;")
                await db.execute("PRAGMA synchronous=NORMAL;")
                await db.execute("PRAGMA foreign_keys=ON;")
                await db.executescript(_CREATE_EVENTS_TABLE)
                await db.executescript(_CREATE_ALERTS_TABLE)

                for idx_sql in _CREATE_EVENTS_INDEXES + _CREATE_ALERTS_INDEXES:
                    await db.execute(idx_sql)

                await db.commit()
            except Exception:
                await db.close()
                raise

            self._db = db
            self._initialized = True
            logger.info("[Audit] LogStore initialized at %s", self._audit_dir)

    async def close(self) -> None:
        """关闭 SQLite 连接."""
        async with self._lifecycle_lock:
            async with self._write_lock:
                if self._db is not None:
                    await self._db.close()
                    self._db = None
                self._initialized = False

    # ── 写入 ────────────────────────────────────────────────────

    async def write_event(self, event: AuditEvent) -> None:
        """写入一条审计事件（JSONL + SQLite 双写）."""
        db = self._require_db()
        data = event.to_dict()
        async with self._write_lock:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO audit_events
                   (event_id, event_type, timestamp, session_id, channel_id,
                    request_id, agent_name, duration_ms, token_usage,
                    error_type, error_detail, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["event_id"],
                    data["event_type"],
                    data["timestamp"],
                    data["session_id"],
                    data["channel_id"],
                    data["request_id"],
                    data["agent_name"],
                    data["duration_ms"],
                    (
                        json.dumps(data["token_usage"], ensure_ascii=False)
                        if data["token_usage"] is not None
                        else None
                    ),
                    data["error_type"],
                    data["error_detail"],
                    json.dumps(data["metadata"] or {}, ensure_ascii=False),
                ),
            )
            inserted = cursor.rowcount > 0
            await cursor.close()
            if not inserted:
                return
            try:
                await asyncio.to_thread(
                    self._append_jsonl_sync, self._events_jsonl, data,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def write_alert(self, alert: Alert) -> None:
        """写入一条告警（JSONL + SQLite 双写）."""
        db = self._require_db()
        data = alert.to_dict()
        async with self._write_lock:
            cursor = await db.execute(
                """INSERT OR IGNORE INTO audit_alerts
                   (alert_id, alert_type, severity, status, triggered_at,
                    rule_name, message, context, resolved_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    data["alert_id"],
                    data["alert_type"],
                    data["severity"],
                    data["status"],
                    data["triggered_at"],
                    data["rule_name"],
                    data["message"],
                    json.dumps(data["context"] or {}, ensure_ascii=False),
                    data["resolved_at"],
                ),
            )
            inserted = cursor.rowcount > 0
            await cursor.close()
            if not inserted:
                return
            try:
                await asyncio.to_thread(
                    self._append_jsonl_sync, self._alerts_jsonl, data,
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def resolve_alert(self, alert_id: str) -> bool:
        """将告警标记为已解决."""
        return await self.set_alert_status(alert_id, AlertStatus.RESOLVED)

    async def suppress_alert(self, alert_id: str) -> bool:
        """Mark an alert as suppressed and return whether it existed."""
        return await self.set_alert_status(alert_id, AlertStatus.SUPPRESSED)

    async def set_alert_status(
        self,
        alert_id: str,
        status: AlertStatus | str,
    ) -> bool:
        """Update alert lifecycle state and return whether one row changed."""
        db = self._require_db()
        normalized = status if isinstance(status, AlertStatus) else AlertStatus(status)
        resolved_at = time.time() if normalized == AlertStatus.RESOLVED else None
        async with self._write_lock:
            cursor = await db.execute(
                "UPDATE audit_alerts SET status = ?, resolved_at = ? WHERE alert_id = ?",
                (normalized.value, resolved_at, alert_id),
            )
            changed = cursor.rowcount > 0
            await cursor.close()
            if changed:
                rows = await db.execute_fetchall(
                    "SELECT * FROM audit_alerts ORDER BY triggered_at ASC, alert_id ASC",
                )
                await asyncio.to_thread(
                    _write_jsonl_records,
                    self._alerts_jsonl,
                    [_row_to_alert(row).to_dict() for row in rows],
                )
            await db.commit()
        return changed

    # ── 查询 ────────────────────────────────────────────────────

    async def query_events(self, filters: dict[str, Any]) -> list[AuditEvent]:
        """按条件查询审计事件.

        filters 支持的字段:
            event_type: str         — 事件类型（如 "chat_error"）
            session_id: str         — 会话 ID
            channel_id: str         — 渠道 ID
            hours: int              — 最近 N 小时
            start_time: float       — 开始时间戳
            end_time: float         — 结束时间戳
            limit: int              — 最大返回条数（默认 500）
        """
        db = self._require_db()

        where_clauses: list[str] = []
        params: list[Any] = []

        if "event_type" in filters:
            where_clauses.append("event_type = ?")
            event_type = filters["event_type"]
            params.append(event_type.value if isinstance(event_type, AuditEventType) else event_type)

        if filters.get("event_types"):
            raw_event_types = filters["event_types"]
            if isinstance(raw_event_types, (str, AuditEventType)):
                raw_event_types = [raw_event_types]
            event_types = [
                item.value if isinstance(item, AuditEventType) else str(item)
                for item in raw_event_types
            ]
            placeholders = ", ".join("?" for _ in event_types)
            where_clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_types)

        if "session_id" in filters:
            where_clauses.append("session_id = ?")
            params.append(filters["session_id"])

        if "channel_id" in filters:
            where_clauses.append("channel_id = ?")
            params.append(filters["channel_id"])

        if "request_id" in filters:
            where_clauses.append("request_id = ?")
            params.append(filters["request_id"])

        if "agent_name" in filters:
            where_clauses.append("agent_name = ?")
            params.append(filters["agent_name"])

        if "error_type" in filters:
            where_clauses.append("error_type = ?")
            params.append(filters["error_type"])

        if filters.get("has_error") is True:
            where_clauses.append("(error_type IS NOT NULL OR event_type = 'chat_error')")

        if "hours" in filters:
            cutoff = _hours_cutoff(filters["hours"])
            where_clauses.append("timestamp >= ?")
            params.append(cutoff)

        if "start_time" in filters:
            where_clauses.append("timestamp >= ?")
            params.append(float(filters["start_time"]))

        if "end_time" in filters:
            where_clauses.append("timestamp <= ?")
            params.append(float(filters["end_time"]))

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        limit = _bounded_limit(filters.get("limit", 500))
        offset = _bounded_offset(filters.get("offset", 0))

        rows = await db.execute_fetchall(
            f"SELECT * FROM audit_events WHERE {where_sql} "
            "ORDER BY timestamp DESC, event_id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )

        return [_row_to_event(row) for row in rows]

    async def query_alerts(self, filters: dict[str, Any]) -> list[Alert]:
        """按条件查询告警."""
        db = self._require_db()

        where_clauses: list[str] = []
        params: list[Any] = []

        if "status" in filters:
            where_clauses.append("status = ?")
            status = filters["status"]
            params.append(status.value if isinstance(status, AlertStatus) else status)

        if "severity" in filters:
            where_clauses.append("severity = ?")
            params.append(filters["severity"])

        if "rule_name" in filters:
            where_clauses.append("rule_name = ?")
            params.append(filters["rule_name"])

        if "alert_type" in filters:
            where_clauses.append("alert_type = ?")
            params.append(filters["alert_type"])

        if "hours" in filters:
            cutoff = _hours_cutoff(filters["hours"])
            where_clauses.append("triggered_at >= ?")
            params.append(cutoff)

        if "start_time" in filters:
            where_clauses.append("triggered_at >= ?")
            params.append(float(filters["start_time"]))

        if "end_time" in filters:
            where_clauses.append("triggered_at <= ?")
            params.append(float(filters["end_time"]))

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        limit = _bounded_limit(filters.get("limit", 500))
        offset = _bounded_offset(filters.get("offset", 0))

        rows = await db.execute_fetchall(
            f"SELECT * FROM audit_alerts WHERE {where_sql} "
            "ORDER BY triggered_at DESC, alert_id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )

        return [_row_to_alert(row) for row in rows]

    async def get_error_summary(self, hours: int = 24) -> dict[str, Any]:
        """获取错误统计摘要."""
        db = self._require_db()
        cutoff = _hours_cutoff(hours)

        rows = await db.execute_fetchall(
            """SELECT event_type, COALESCE(error_type, 'unknown'), COUNT(*) as count
               FROM audit_events
               WHERE timestamp >= ?
                 AND (error_type IS NOT NULL OR event_type = 'chat_error')
               GROUP BY event_type, error_type
               ORDER BY count DESC""",
            (cutoff,),
        )

        result: dict[str, Any] = {"hours": hours, "error_breakdown": []}
        for row in rows:
            result["error_breakdown"].append({
                "event_type": row[0],
                "error_type": row[1],
                "count": row[2],
            })
        return result

    async def get_token_usage_summary(self, hours: int = 24) -> dict[str, Any]:
        """获取 Token 消耗统计."""
        db = self._require_db()
        cutoff = _hours_cutoff(hours)

        rows = await db.execute_fetchall(
            """SELECT session_id, channel_id, token_usage
               FROM audit_events
               WHERE timestamp >= ? AND token_usage IS NOT NULL
               ORDER BY timestamp DESC""",
            (cutoff,),
        )

        total_prompt = 0
        total_completion = 0
        total_total = 0
        by_channel: dict[str, dict[str, int]] = {}

        for row in rows:
            token_str = row[2]
            if not token_str:
                continue
            token_data = _decode_json_dict(token_str)
            if not token_data:
                continue

            prompt = _safe_int(token_data.get("prompt_tokens") or token_data.get("prompt"))
            completion = _safe_int(
                token_data.get("completion_tokens") or token_data.get("completion"),
            )
            total = _safe_int(token_data.get("total_tokens") or token_data.get("total"))

            total_prompt += prompt
            total_completion += completion
            total_total += total

            ch = row[1] or "unknown"
            if ch not in by_channel:
                by_channel[ch] = {"prompt": 0, "completion": 0, "total": 0}
            by_channel[ch]["prompt"] += prompt
            by_channel[ch]["completion"] += completion
            by_channel[ch]["total"] += total

        return {
            "hours": hours,
            "total": {
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
                "total_tokens": total_total,
            },
            "by_channel": by_channel,
        }

    async def get_session_summary(self, session_id: str) -> dict[str, Any] | None:
        """获取一个会话的审计摘要."""
        db = self._require_db()

        rows = await db.execute_fetchall(
            """SELECT event_type, COUNT(*) as count
               FROM audit_events WHERE session_id = ?
               GROUP BY event_type""",
            (session_id,),
        )

        if not rows:
            return None

        event_counts = {row[0]: row[1] for row in rows}

        token_rows = await db.execute_fetchall(
            """SELECT token_usage FROM audit_events
               WHERE session_id = ? AND token_usage IS NOT NULL""",
            (session_id,),
        )

        total_tokens: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        for row in token_rows:
            td = _decode_json_dict(row[0])
            if not td:
                continue
            total_tokens["prompt"] += _safe_int(
                td.get("prompt_tokens") or td.get("prompt"),
            )
            total_tokens["completion"] += _safe_int(
                td.get("completion_tokens") or td.get("completion"),
            )
            total_tokens["total"] += _safe_int(
                td.get("total_tokens") or td.get("total"),
            )

        time_rows = await db.execute_fetchall(
            """SELECT MIN(timestamp), MAX(timestamp), AVG(duration_ms), MAX(duration_ms)
               FROM audit_events WHERE session_id = ?""",
            (session_id,),
        )

        start_time = time_rows[0][0] if time_rows else 0
        end_time = time_rows[0][1] if time_rows else 0
        duration = (end_time - start_time) if start_time and end_time else None

        average_duration_ms = time_rows[0][2] if time_rows else None
        max_duration_ms = time_rows[0][3] if time_rows else None

        ch_row = await db.execute_fetchall(
            """SELECT channel_id FROM audit_events
               WHERE session_id = ? AND channel_id IS NOT NULL
               ORDER BY timestamp ASC LIMIT 1""",
            (session_id,),
        )
        channel_id = ch_row[0][0] if ch_row else None

        return {
            "session_id": session_id,
            "channel_id": channel_id,
            "start_time": start_time,
            "end_time": end_time,
            "duration_seconds": duration,
            "event_counts": event_counts,
            "total_requests": event_counts.get("chat_request", 0),
            "total_errors": event_counts.get("chat_error", 0),
            "total_tokens": total_tokens,
            "average_duration_ms": average_duration_ms,
            "max_duration_ms": max_duration_ms,
        }

    async def list_sessions(
        self,
        hours: int = 24,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List recent sessions with compact operational statistics."""
        db = self._require_db()
        cutoff = _hours_cutoff(hours)
        rows = await db.execute_fetchall(
            """SELECT
                   session_id,
                   MAX(channel_id) AS channel_id,
                   MIN(timestamp) AS start_time,
                   MAX(timestamp) AS end_time,
                   COUNT(*) AS total_events,
                   SUM(CASE WHEN event_type = 'chat_request' THEN 1 ELSE 0 END)
                       AS total_requests,
                   SUM(CASE WHEN event_type = 'chat_error' OR error_type IS NOT NULL
                            THEN 1 ELSE 0 END) AS total_errors,
                   AVG(duration_ms) AS average_duration_ms,
                   MAX(duration_ms) AS max_duration_ms
               FROM audit_events
               WHERE session_id IS NOT NULL AND session_id != '' AND timestamp >= ?
               GROUP BY session_id
               ORDER BY end_time DESC
               LIMIT ? OFFSET ?""",
            (cutoff, _bounded_limit(limit), _bounded_offset(offset)),
        )
        return [
            {
                "session_id": row[0],
                "channel_id": row[1],
                "start_time": row[2],
                "end_time": row[3],
                "duration_seconds": max(0.0, row[3] - row[2]),
                "total_events": row[4],
                "total_requests": row[5],
                "total_errors": row[6],
                "average_duration_ms": row[7],
                "max_duration_ms": row[8],
            }
            for row in rows
        ]

    async def get_overview(self, hours: int = 24) -> dict[str, Any]:
        """Build a high-level report for dashboards and the CLI."""
        db = self._require_db()
        cutoff = _hours_cutoff(hours)
        totals = await db.execute_fetchall(
            """SELECT
                   COUNT(*),
                   COUNT(DISTINCT session_id),
                   SUM(CASE WHEN event_type = 'chat_error' OR error_type IS NOT NULL
                            THEN 1 ELSE 0 END),
                   AVG(duration_ms),
                   MAX(duration_ms)
               FROM audit_events WHERE timestamp >= ?""",
            (cutoff,),
        )
        event_rows = await db.execute_fetchall(
            """SELECT event_type, COUNT(*) FROM audit_events
               WHERE timestamp >= ? GROUP BY event_type ORDER BY COUNT(*) DESC""",
            (cutoff,),
        )
        alert_rows = await db.execute_fetchall(
            """SELECT status, severity, COUNT(*) FROM audit_alerts
               WHERE triggered_at >= ? GROUP BY status, severity""",
            (cutoff,),
        )
        row = totals[0] if totals else (0, 0, 0, None, None)
        total_events = row[0] or 0
        total_errors = row[2] or 0
        alerts: dict[str, dict[str, int]] = {}
        for status, severity, count in alert_rows:
            alerts.setdefault(status, {})[severity] = count

        token_usage = await self.get_token_usage_summary(hours)
        return {
            "hours": hours,
            "total_events": total_events,
            "total_sessions": row[1] or 0,
            "total_errors": total_errors,
            "error_event_ratio": total_errors / total_events if total_events else 0.0,
            "average_duration_ms": row[3],
            "max_duration_ms": row[4],
            "event_counts": {event_type: count for event_type, count in event_rows},
            "alerts": alerts,
            "token_usage": token_usage["total"],
        }

    async def get_event_timeseries(
        self,
        hours: int = 24,
        bucket_minutes: int = 60,
    ) -> list[dict[str, Any]]:
        """Aggregate event volume, errors and latency into chronological buckets.

        Empty buckets are omitted. Consumers can use ``bucket_start`` and
        ``bucket_end`` to insert gaps when drawing a chart.
        """
        db = self._require_db()
        normalized_minutes = min(24 * 60, max(1, _safe_int(bucket_minutes)))
        bucket_seconds = normalized_minutes * 60
        rows = await db.execute_fetchall(
            """SELECT
                   CAST(timestamp / ? AS INTEGER) * ? AS bucket_start,
                   COUNT(*) AS total_events,
                   SUM(CASE WHEN event_type = 'chat_request' THEN 1 ELSE 0 END)
                       AS total_requests,
                   SUM(CASE WHEN event_type = 'chat_response'
                                  OR event_type = 'memory_after_chat'
                            THEN 1 ELSE 0 END) AS total_responses,
                   SUM(CASE WHEN event_type = 'chat_error' OR error_type IS NOT NULL
                            THEN 1 ELSE 0 END) AS total_errors,
                   AVG(duration_ms) AS average_duration_ms,
                   MAX(duration_ms) AS max_duration_ms
               FROM audit_events
               WHERE timestamp >= ?
               GROUP BY CAST(timestamp / ? AS INTEGER)
               ORDER BY bucket_start ASC""",
            (
                bucket_seconds,
                bucket_seconds,
                _hours_cutoff(hours),
                bucket_seconds,
            ),
        )
        return [
            {
                "bucket_start": row[0],
                "bucket_end": row[0] + bucket_seconds,
                "bucket_minutes": normalized_minutes,
                "total_events": row[1],
                "total_requests": row[2],
                "total_responses": row[3],
                "total_errors": row[4],
                "error_event_ratio": row[4] / row[1] if row[1] else 0.0,
                "average_duration_ms": row[5],
                "max_duration_ms": row[6],
            }
            for row in rows
        ]

    async def cleanup_old_events(self, retention_days: int) -> int:
        """清理过期审计数据（JSONL + SQLite）."""
        if retention_days < 0:
            raise ValueError("retention_days must be greater than or equal to zero")
        db = self._require_db()
        cutoff = time.time() - retention_days * 86400
        deleted = 0

        async with self._write_lock:
            event_cursor = await db.execute(
                "DELETE FROM audit_events WHERE timestamp < ?", (cutoff,),
            )
            deleted += max(0, event_cursor.rowcount)
            await event_cursor.close()
            alert_cursor = await db.execute(
                "DELETE FROM audit_alerts WHERE triggered_at < ? AND status = 'resolved'",
                (cutoff,),
            )
            deleted += max(0, alert_cursor.rowcount)
            await alert_cursor.close()
            await db.commit()

            event_rows = await db.execute_fetchall(
                "SELECT * FROM audit_events ORDER BY timestamp ASC, event_id ASC",
            )
            alert_rows = await db.execute_fetchall(
                "SELECT * FROM audit_alerts ORDER BY triggered_at ASC, alert_id ASC",
            )
            await asyncio.to_thread(
                _write_jsonl_records,
                self._events_jsonl,
                [_row_to_event(row).to_dict() for row in event_rows],
            )
            await asyncio.to_thread(
                _write_jsonl_records,
                self._alerts_jsonl,
                [_row_to_alert(row).to_dict() for row in alert_rows],
            )

        logger.info("[Audit] Cleaned up %d records older than %d days", deleted, retention_days)
        return deleted

    async def get_status(self) -> dict[str, Any]:
        """获取审计系统状态概览."""
        if self._db is None:
            return {"status": "not_initialized"}

        event_count = await self._db.execute_fetchall(
            "SELECT COUNT(*) FROM audit_events",
        )
        alert_count = await self._db.execute_fetchall(
            "SELECT COUNT(*) FROM audit_alerts WHERE status = 'active'",
        )
        last_event = await self._db.execute_fetchall(
            "SELECT MAX(timestamp) FROM audit_events",
        )

        return {
            "status": "running",
            "audit_dir": str(self._audit_dir),
            "total_events": event_count[0][0] if event_count else 0,
            "active_alerts": alert_count[0][0] if alert_count else 0,
            "last_event_timestamp": last_event[0][0] if last_event and last_event[0][0] else None,
        }

    async def export_to_jsonl(self, output_path: Path, filters: dict[str, Any]) -> int:
        """导出审计事件为 JSONL 文件."""
        events = await self.query_events(filters)
        await asyncio.to_thread(_write_jsonl_export, output_path, events)
        return len(events)

    async def export_to_csv(self, output_path: Path, filters: dict[str, Any]) -> int:
        """Export events to a spreadsheet-friendly UTF-8 CSV file."""
        events = await self.query_events(filters)
        await asyncio.to_thread(_write_csv_export, output_path, events)
        return len(events)

    # ── 内部辅助 ────────────────────────────────────────────────

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None or not self._initialized:
            raise RuntimeError("LogStore is not initialized; call await initialize() first")
        return self._db

    @staticmethod
    def _append_jsonl_sync(path: Path, data: dict[str, Any]) -> None:
        """Append a JSONL record; callers serialize access with ``_write_lock``."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as file:
            file.write(json.dumps(data, ensure_ascii=False) + "\n")

# ── 行 → 模型 转换 ─────────────────────────────────────────────

_EVENT_COLUMNS = [
    "event_id", "event_type", "timestamp", "session_id", "channel_id",
    "request_id", "agent_name", "duration_ms", "token_usage",
    "error_type", "error_detail", "metadata",
]

_ALERT_COLUMNS = [
    "alert_id", "alert_type", "severity", "status", "triggered_at",
    "rule_name", "message", "context", "resolved_at",
]


def _row_to_event(row: tuple) -> AuditEvent:
    """SQLite 行 → AuditEvent."""
    d = dict(zip(_EVENT_COLUMNS, row))
    token_usage = d.get("token_usage")
    d["token_usage"] = _decode_json_dict(token_usage) if token_usage is not None else None
    d["metadata"] = _decode_json_dict(d.get("metadata"))
    return AuditEvent.from_dict(d)


def _row_to_alert(row: tuple) -> Alert:
    """SQLite 行 → Alert."""
    d = dict(zip(_ALERT_COLUMNS, row))
    d["context"] = _decode_json_dict(d.get("context"))
    return Alert.from_dict(d)


def _bounded_limit(value: Any, default: int = 500) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return min(10_000, max(1, parsed))


def _bounded_offset(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = 0
    return max(0, parsed)


def _hours_cutoff(value: Any) -> float:
    try:
        hours = max(0.0, float(value))
    except (TypeError, ValueError, OverflowError):
        hours = 0.0
    return time.time() - hours * 3600


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _decode_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        decoded = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _write_jsonl_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_jsonl_export(output_path: Path, events: list[AuditEvent]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        for event in events:
            file.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")


def _write_csv_export(output_path: Path, events: list[AuditEvent]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=_EVENT_COLUMNS)
        writer.writeheader()
        for event in events:
            row = event.to_dict()
            row["token_usage"] = json.dumps(row["token_usage"] or {}, ensure_ascii=False)
            row["metadata"] = json.dumps(row["metadata"] or {}, ensure_ascii=False)
            writer.writerow(row)
