"""Observability database — audit rules storage via openjiuwen_runtime DBHandler.

Follows the same pattern as jiuwenclaw_manager.infrastructure.db.
Environment variables: OBSERVABILITY_DB_TYPE / OBSERVABILITY_SQLITE_PATH /
                       OBSERVABILITY_DB_HOST / OBSERVABILITY_DB_PORT /
                       OBSERVABILITY_DB_USER / OBSERVABILITY_DB_PASSWORD /
                       OBSERVABILITY_DB_NAME
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.mysql_handler import MySQLHandler
from openjiuwen_runtime.foundation.db.postgresql_handler import PostgreSQLHandler
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler

from jiuwenclaw_observability.schema import AUDIT_RULES_TABLE, DEFAULT_RULES

logger = logging.getLogger("jiuwenclaw-observability")

_PKG_ROOT = Path(__file__).resolve().parents[2]

_db_handler: DBHandler | None = None


def _sqlite_handler() -> SQLiteHandler:
    raw = os.getenv("OBSERVABILITY_SQLITE_PATH", "observability.db").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = (_PKG_ROOT / raw).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteHandler(str(p.as_posix()))


def _mysql_handler() -> MySQLHandler:
    return MySQLHandler(
        host=os.getenv("OBSERVABILITY_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("OBSERVABILITY_DB_PORT", "3306")),
        user=os.getenv("OBSERVABILITY_DB_USER", "root"),
        password=os.getenv("OBSERVABILITY_DB_PASSWORD", "root"),
        database=os.getenv("OBSERVABILITY_DB_NAME", "observability"),
    )


def _pg_handler() -> PostgreSQLHandler:
    return PostgreSQLHandler(
        host=os.getenv("OBSERVABILITY_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("OBSERVABILITY_DB_PORT", "5432")),
        user=os.getenv("OBSERVABILITY_DB_USER", "root"),
        password=os.getenv("OBSERVABILITY_DB_PASSWORD", "root"),
        database=os.getenv("OBSERVABILITY_DB_NAME", "observability"),
        schema=os.getenv("OBSERVABILITY_PG_SCHEMA", "public"),
    )


async def init_db() -> DBHandler:
    """Create and connect DB handler, init table, seed defaults."""
    global _db_handler

    db_type = os.getenv("OBSERVABILITY_DB_TYPE", "sqlite").strip().lower()
    logger.info("Using observability database: %s", db_type)

    if db_type == "sqlite":
        _db_handler = _sqlite_handler()
    elif db_type == "mysql":
        _db_handler = _mysql_handler()
    elif db_type in ("postgresql", "postgres", "pg"):
        _db_handler = _pg_handler()
    else:
        raise ValueError(f"Unsupported db_type: {db_type}")

    await _db_handler.init_database()
    await _db_handler.connect()
    await _db_handler.init_table(AUDIT_RULES_TABLE)

    # Seed default rules if table is empty
    existing = await _db_handler.list_records("audit_rules", limit=1)
    if not existing:
        now = datetime.now(tz=ZoneInfo(os.getenv("TZ", "UTC")))
        for rule in DEFAULT_RULES:
            await _db_handler.create("audit_rules", {**rule, "created_at": now, "updated_at": now})
        logger.info("Seeded %d default audit rules", len(DEFAULT_RULES))

    return _db_handler


def get_db() -> DBHandler:
    if _db_handler is None:
        raise RuntimeError("Observability DB not initialized; call init_db() first")
    return _db_handler


async def get_rules_for_detector(detector: str) -> list[dict[str, Any]]:
    """Get enabled rules for a detector (used by agentserver to reload)."""
    records = await get_db().list_records("audit_rules", filters={"detector": detector, "enabled": 1}, limit=1000)
    return [r.to_dict() if hasattr(r, "to_dict") else dict(r) for r in records]
