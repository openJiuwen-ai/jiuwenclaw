# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""审计日志持久化 — JSONL 实时追加 + SQLite 结构化查询.

设计要点:
- JSONL 用于实时追加写入（零延迟，不阻塞 Hook 回调）
- SQLite 用于按维度高效查询（时间、类型、会话、渠道）
- 两者互为备份，JSONL 是原始数据，SQLite 是索引视图
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import aiosqlite

from .models import AuditEvent, AuditEventType, Alert

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
]

_CREATE_ALERTS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_alerts_triggered_at ON audit_alerts(triggered_at);",
    "CREATE INDEX IF NOT EXISTS idx_alerts_status ON audit_alerts(status);",
    "CREATE INDEX IF NOT EXISTS idx_alerts_severity ON audit_alerts(severity);",
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
        self._write_lock = threading.Lock()
        self._initialized = False

    @property
    def audit_dir(self) -> Path:
        return self._audit_dir

    async def initialize(self) -> None:
        """创建目录、建表、建索引."""
        self._audit_dir.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(str(self._db_path))
        await self._db.execute("PRAGMA journal_mode=WAL;")
        await self._db.execute("PRAGMA synchronous=NORMAL;")

        await self._db.executescript(_CREATE_EVENTS_TABLE)
        await self._db.executescript(_CREATE_ALERTS_TABLE)

        for idx_sql in _CREATE_EVENTS_INDEXES + _CREATE_ALERTS_INDEXES:
            await self._db.execute(idx_sql)

        await self._db.commit()
        self._initialized = True
        logger.info("[Audit] LogStore initialized at %s", self._audit_dir)

    async def close(self) -> None:
        """关闭 SQLite 连接."""
        if self._db is not None:
            await self._db.close()
            self._db = None
        self._initialized = False

    # ── 写入 ────────────────────────────────────────────────────

    async def write_event(self, event: AuditEvent) -> None:
        """写入一条审计事件（JSONL + SQLite 双写）."""
        data = event.to_dict()
        # JSONL 追加（线程安全）
        self._append_jsonl(self._events_jsonl, data)
        # SQLite 写入
        if self._db is not None:
            await self._db.execute(
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
                    json.dumps(data["token_usage"] or {}, ensure_ascii=False),
                    data["error_type"],
                    data["error_detail"],
                    json.dumps(data["metadata"] or {}, ensure_ascii=False),
                ),
            )
            await self._db.commit()

    async def write_alert(self, alert: Alert) -> None:
        """写入一条告警（JSONL + SQLite 双写）."""
        data = alert.to_dict()
        self._append_jsonl(self._alerts_jsonl, data)
        if self._db is not None:
            await self._db.execute(
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
            await self._db.commit()

    async def resolve_alert(self, alert_id: str) -> None:
        """将告警标记为已解决."""
        if self._db is not None:
            await self._db.execute(
                "UPDATE audit_alerts SET status = ?, resolved_at = ? WHERE alert_id = ?",
                ("resolved", time.time(), alert_id),
            )
            await self._db.commit()

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
        if self._db is None:
            return []

        where_clauses: list[str] = []
        params: list[Any] = []

        if "event_type" in filters:
            where_clauses.append("event_type = ?")
            params.append(filters["event_type"])

        if "session_id" in filters:
            where_clauses.append("session_id = ?")
            params.append(filters["session_id"])

        if "channel_id" in filters:
            where_clauses.append("channel_id = ?")
            params.append(filters["channel_id"])

        if "hours" in filters:
            cutoff = time.time() - int(filters["hours"]) * 3600
            where_clauses.append("timestamp >= ?")
            params.append(cutoff)

        if "start_time" in filters:
            where_clauses.append("timestamp >= ?")
            params.append(float(filters["start_time"]))

        if "end_time" in filters:
            where_clauses.append("timestamp <= ?")
            params.append(float(filters["end_time"]))

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        limit = int(filters.get("limit", 500))

        rows = await self._db.execute_fetchall(
            f"SELECT * FROM audit_events WHERE {where_sql} "
            f"ORDER BY timestamp DESC LIMIT {limit}",
            params,
        )

        return [_row_to_event(row) for row in rows]

    async def query_alerts(self, filters: dict[str, Any]) -> list[Alert]:
        """按条件查询告警."""
        if self._db is None:
            return []

        where_clauses: list[str] = []
        params: list[Any] = []

        if "status" in filters:
            where_clauses.append("status = ?")
            params.append(filters["status"])

        if "severity" in filters:
            where_clauses.append("severity = ?")
            params.append(filters["severity"])

        if "rule_name" in filters:
            where_clauses.append("rule_name = ?")
            params.append(filters["rule_name"])

        if "hours" in filters:
            cutoff = time.time() - int(filters["hours"]) * 3600
            where_clauses.append("triggered_at >= ?")
            params.append(cutoff)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        limit = int(filters.get("limit", 500))

        rows = await self._db.execute_fetchall(
            f"SELECT * FROM audit_alerts WHERE {where_sql} "
            f"ORDER BY triggered_at DESC LIMIT {limit}",
            params,
        )

        return [_row_to_alert(row) for row in rows]

    async def get_error_summary(self, hours: int = 24) -> dict[str, Any]:
        """获取错误统计摘要."""
        if self._db is None:
            return {}
        cutoff = time.time() - hours * 3600

        rows = await self._db.execute_fetchall(
            """SELECT event_type, error_type, COUNT(*) as count
               FROM audit_events
               WHERE timestamp >= ? AND error_type IS NOT NULL
               GROUP BY event_type, error_type
               ORDER BY count DESC""",
            (cutoff,),
        )

        result: dict[str, Any] = {"hours": hours, "error_breakdown": []}
        for row in rows:
            result["error_breakdown"].append({
                "event_type": row[1],
                "error_type": row[2],
                "count": row[3],
            })
        return result

    async def get_token_usage_summary(self, hours: int = 24) -> dict[str, Any]:
        """获取 Token 消耗统计."""
        if self._db is None:
            return {}
        cutoff = time.time() - hours * 3600

        rows = await self._db.execute_fetchall(
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
            try:
                token_data = json.loads(token_str)
            except json.JSONDecodeError:
                continue

            prompt = int(token_data.get("prompt_tokens") or token_data.get("prompt") or 0)
            completion = int(token_data.get("completion_tokens") or token_data.get("completion") or 0)
            total = int(token_data.get("total_tokens") or token_data.get("total") or 0)

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
        if self._db is None:
            return None

        rows = await self._db.execute_fetchall(
            """SELECT event_type, COUNT(*) as count
               FROM audit_events WHERE session_id = ?
               GROUP BY event_type""",
            (session_id,),
        )

        if not rows:
            return None

        event_counts = {row[1]: row[2] for row in rows}

        token_rows = await self._db.execute_fetchall(
            """SELECT token_usage FROM audit_events
               WHERE session_id = ? AND token_usage IS NOT NULL""",
            (session_id,),
        )

        total_tokens: dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        for row in token_rows:
            try:
                td = json.loads(row[0])
                total_tokens["prompt"] += int(td.get("prompt_tokens") or td.get("prompt") or 0)
                total_tokens["completion"] += int(td.get("completion_tokens") or td.get("completion") or 0)
                total_tokens["total"] += int(td.get("total_tokens") or td.get("total") or 0)
            except (json.JSONDecodeError, TypeError):
                continue

        time_rows = await self._db.execute_fetchall(
            """SELECT MIN(timestamp), MAX(timestamp) FROM audit_events WHERE session_id = ?""",
            (session_id,),
        )

        start_time = time_rows[0][0] if time_rows else 0
        end_time = time_rows[0][1] if time_rows else 0
        duration = (end_time - start_time) if start_time and end_time else None

        ch_row = await self._db.execute_fetchall(
            """SELECT channel_id FROM audit_events WHERE session_id = ? LIMIT 1""",
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
        }

    async def cleanup_old_events(self, retention_days: int) -> int:
        """清理过期审计数据（JSONL + SQLite）."""
        cutoff = time.time() - retention_days * 86400
        deleted = 0

        if self._db is not None:
            await self._db.execute(
                "DELETE FROM audit_events WHERE timestamp < ?", (cutoff,),
            )
            deleted += self._db.total_changes
            await self._db.execute(
                "DELETE FROM audit_alerts WHERE triggered_at < ? AND status = 'resolved'",
                (cutoff,),
            )
            deleted += self._db.total_changes
            await self._db.commit()

        # JSONL 按行过滤（保留新数据）
        self._filter_jsonl_by_timestamp(self._events_jsonl, cutoff)
        self._filter_jsonl_by_timestamp(self._alerts_jsonl, cutoff, key="triggered_at")

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
        output_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                count += 1
        return count

    # ── 内部辅助 ────────────────────────────────────────────────

    def _append_jsonl(self, path: Path, data: dict[str, Any]) -> None:
        """原子追加一行 JSONL（线程安全）."""
        with self._write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def _filter_jsonl_by_timestamp(
        self, path: Path, cutoff: float, key: str = "timestamp",
    ) -> None:
        """过滤 JSONL 文件中时间戳早于 cutoff 的行."""
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return

        kept: list[str] = []
        for line in lines:
            try:
                obj = json.loads(line)
                ts = float(obj.get(key, 0))
                if ts >= cutoff:
                    kept.append(line)
            except (json.JSONDecodeError, ValueError, TypeError):
                kept.append(line)  # 无法解析的行保留

        try:
            path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        except OSError:
            logger.warning("[Audit] Failed to filter JSONL file: %s", path)


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
    try:
        d["token_usage"] = json.loads(d.get("token_usage") or "{}")
    except json.JSONDecodeError:
        d["token_usage"] = {}
    try:
        d["metadata"] = json.loads(d.get("metadata") or "{}")
    except json.JSONDecodeError:
        d["metadata"] = {}
    return AuditEvent.from_dict(d)


def _row_to_alert(row: tuple) -> Alert:
    """SQLite 行 → Alert."""
    d = dict(zip(_ALERT_COLUMNS, row))
    try:
        d["context"] = json.loads(d.get("context") or "{}")
    except json.JSONDecodeError:
        d["context"] = {}
    return Alert.from_dict(d)
