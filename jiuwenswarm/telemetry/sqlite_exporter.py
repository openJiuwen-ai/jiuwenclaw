# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SQLite exporter for OpenTelemetry traces."""

import json
import logging
import sqlite3
import threading
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

logger = logging.getLogger(__name__)


class SQLiteSpanExporter(SpanExporter):
    """SQLite exporter for OpenTelemetry spans.

    Stores spans in a local SQLite database for offline analysis,
    debugging, and performance monitoring.
    """

    def __init__(self, db_path: str = "traces.db"):
        """Initialize SQLite exporter.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = db_path
        self._local = threading.local()  # Thread-local storage for connections
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection with WAL mode.

        Returns:
            SQLite connection for current thread.
        """
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            conn = sqlite3.connect(self.db_path)
            # Enable WAL mode for better concurrent access
            conn.execute("PRAGMA journal_mode=WAL")
            # Enable foreign keys
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.connection = conn
        return self._local.connection

    def _init_db(self) -> None:
        """Initialize database schema with table and indexes."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Create spans table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS spans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                span_id TEXT NOT NULL,
                parent_span_id TEXT,
                name TEXT NOT NULL,
                kind INTEGER NOT NULL,
                start_time_ns INTEGER NOT NULL,
                end_time_ns INTEGER,
                duration_ns INTEGER,
                status_code TEXT DEFAULT 'UNSET',
                status_description TEXT,
                attributes TEXT,
                events TEXT,
                links TEXT,
                resource TEXT,
                scope_name TEXT,
                scope_version TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create indexes for common queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_spans_trace_id
            ON spans(trace_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_spans_name
            ON spans(name)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_spans_start_time
            ON spans(start_time_ns)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_spans_parent_span_id
            ON spans(parent_span_id)
        """)

        conn.commit()

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        """Export spans to SQLite database.

        Args:
            spans: List of spans to export.

        Returns:
            Export result (SUCCESS or FAILURE).
        """
        if not spans:
            return SpanExportResult.SUCCESS

        conn = self._get_connection()
        try:
            for span in spans:
                if span.context is None:
                    logger.warning(f"Skipping span '{span.name}' with no context")
                    continue
                self._insert_span(conn, span)
            conn.commit()
            return SpanExportResult.SUCCESS
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to export spans: {e}")
            return SpanExportResult.FAILURE

    def _insert_span(self, conn: sqlite3.Connection, span: ReadableSpan) -> None:
        """Insert a single span into the database.

        Args:
            conn: SQLite connection.
            span: Span to insert.
        """
        cursor = conn.cursor()

        # Convert trace_id and span_id to hex format
        trace_id_hex = format(span.context.trace_id, '032x')
        span_id_hex = format(span.context.span_id, '016x')

        # Convert parent_span_id to hex if present
        parent_span_id_hex = None
        if span.parent:
            parent_span_id_hex = format(span.parent.span_id, '016x')

        # Calculate duration
        duration_ns = None
        if span.end_time and span.start_time:
            duration_ns = span.end_time - span.start_time

        # Serialize attributes to JSON (preserve Chinese characters as-is)
        attributes_json = json.dumps(dict(span.attributes), ensure_ascii=False) if span.attributes else None

        # Serialize events to JSON (preserve Chinese characters as-is)
        events_json = None
        if span.events:
            events_data = [
                {
                    "name": event.name,
                    "timestamp": event.timestamp,
                    "attributes": dict(event.attributes) if event.attributes else {}
                }
                for event in span.events
            ]
            events_json = json.dumps(events_data, ensure_ascii=False)

        # Serialize links to JSON (preserve Chinese characters as-is)
        links_json = None
        if span.links:
            links_data = [
                {
                    "trace_id": format(link.context.trace_id, '032x'),
                    "span_id": format(link.context.span_id, '016x'),
                    "attributes": dict(link.attributes) if link.attributes else {}
                }
                for link in span.links
            ]
            links_json = json.dumps(links_data, ensure_ascii=False)

        # Serialize resource to JSON (preserve Chinese characters as-is)
        resource_json = json.dumps(dict(span.resource.attributes), ensure_ascii=False) if span.resource else None

        # Extract scope info
        scope_name = None
        scope_version = None
        if span.instrumentation_scope:
            scope_name = span.instrumentation_scope.name
            scope_version = span.instrumentation_scope.version

        # Extract status info
        status_code = "UNSET"
        status_description = None
        if span.status:
            status_code = span.status.status_code.name
            status_description = span.status.description

        cursor.execute("""
            INSERT INTO spans (
                trace_id, span_id, parent_span_id, name, kind,
                start_time_ns, end_time_ns, duration_ns,
                status_code, status_description,
                attributes, events, links, resource,
                scope_name, scope_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trace_id_hex,
            span_id_hex,
            parent_span_id_hex,
            span.name,
            span.kind.value,
            span.start_time,
            span.end_time,
            duration_ns,
            status_code,
            status_description,
            attributes_json,
            events_json,
            links_json,
            resource_json,
            scope_name,
            scope_version
        ))

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Force flush - always returns True since writes are immediate.

        Args:
            timeout_millis: Timeout in milliseconds (ignored).

        Returns:
            Always True for immediate writes.
        """
        return True

    def shutdown(self) -> None:
        """Shutdown the exporter and close database connection."""
        if hasattr(self._local, 'connection') and self._local.connection is not None:
            self._local.connection.close()
            self._local.connection = None


def query_spans(
    db_path: str,
    span_name: str | None = None,
    trace_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Query spans from SQLite database.

    Args:
        db_path: Path to SQLite database
        span_name: Filter by span name (optional)
        trace_id: Filter by trace ID (optional)
        limit: Maximum number of results
        offset: Offset for pagination

    Returns:
        List of span dictionaries with parsed JSON fields
    """
    from typing import Any

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    query = "SELECT * FROM spans"
    params: list[Any] = []

    if span_name:
        query += " WHERE name = ?"
        params.append(span_name)

    if trace_id:
        if params:
            query += " AND trace_id = ?"
        else:
            query += " WHERE trace_id = ?"
        params.append(trace_id)

    query += " ORDER BY start_time_ns DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    rows = cursor.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        result = dict(row)
        # Parse JSON fields
        if result.get("attributes"):
            result["attributes"] = json.loads(result["attributes"])
        if result.get("events"):
            result["events"] = json.loads(result["events"])
        if result.get("links"):
            result["links"] = json.loads(result["links"])
        if result.get("resource"):
            result["resource"] = json.loads(result["resource"])
        results.append(result)

    conn.close()
    return results

def read_flat_span(db_path: str, trace_id: str) -> list[dict[str, Any]]:
    """Read all spans for trace_id from SQLite, sorted by start_time_ns.

    Returns flat list of span dicts (no children, no depth).
    Compatible with otel_adapter._read_flat_spans format.
    """
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        spans = cursor.execute(
            "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_time_ns",
            (trace_id,),
        ).fetchall()
        result = [dict(r) for r in spans]
        conn.close()
        return result
    except Exception as exc:
        logger.warning("read_flat_span failed: %s", exc)
        return []

def get_trace_tree(db_path: str, trace_id: str) -> list[dict[str, Any]]:
    """Get complete trace tree with depth information.

    Args:
        db_path: Path to SQLite database
        trace_id: Trace ID to query (32-char hex string)

    Returns:
        List of root spans with nested children and depth field
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute(
        "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_time_ns",
        (trace_id,)
    )
    rows = cursor.fetchall()

    # Build span map (process rows before closing connection
    # so dict(row) can reliably access column names)
    span_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        span = dict(row)
        # Parse JSON fields
        if span.get("attributes"):
            span["attributes"] = json.loads(span["attributes"])
        if span.get("events"):
            span["events"] = json.loads(span["events"])
        if span.get("links"):
            span["links"] = json.loads(span["links"])
        if span.get("resource"):
            span["resource"] = json.loads(span["resource"])
        span["children"] = []
        span_map[span["span_id"]] = span

    conn.close()

    # Build tree
    roots: list[dict[str, Any]] = []
    for span in span_map.values():
        parent_id = span.get("parent_span_id")
        if parent_id and parent_id in span_map:
            span_map[parent_id]["children"].append(span)
        else:
            roots.append(span)

    # Calculate depth recursively
    def set_depth(node: dict[str, Any], depth: int) -> None:
        node["depth"] = depth
        for child in node["children"]:
            set_depth(child, depth + 1)

    for root in roots:
        set_depth(root, 0)

    return roots


def get_span_statistics(db_path: str) -> dict[str, Any]:
    """Get span statistics from database.

    Args:
        db_path: Path to SQLite database

    Returns:
        Dictionary with statistics:
        - total_spans: Total number of spans
        - total_traces: Number of unique traces
        - span_names: List of {name, count, avg_duration_ms}
        - time_range: {start, end}
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Total spans
    cursor.execute("SELECT COUNT(*) as total FROM spans")
    total_spans = cursor.fetchone()["total"]

    # Total traces
    cursor.execute("SELECT COUNT(DISTINCT trace_id) as total FROM spans")
    total_traces = cursor.fetchone()["total"]

    # Span names breakdown
    cursor.execute("""
        SELECT
            name,
            COUNT(*) as count,
            AVG(duration_ns) / 1e6 as avg_duration_ms
        FROM spans
        GROUP BY name
        ORDER BY count DESC
    """)
    span_names: list[dict[str, Any]] = [dict(row) for row in cursor.fetchall()]

    # Time range
    cursor.execute("""
        SELECT
            MIN(datetime(start_time_ns / 1e9, 'unixepoch')) as start,
            MAX(datetime(start_time_ns / 1e9, 'unixepoch')) as end
        FROM spans
    """)
    time_row = cursor.fetchone()
    time_range: dict[str, Any] = {
        "start": time_row["start"],
        "end": time_row["end"],
    }

    conn.close()

    return {
        "total_spans": total_spans,
        "total_traces": total_traces,
        "span_names": span_names,
        "time_range": time_range,
    }
