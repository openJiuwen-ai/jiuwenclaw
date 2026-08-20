"""Rule loader — reads audit rules from observability DB (async, via DBHandler).

Uses the same DBHandler classes as manager (SQLiteHandler / MySQLHandler /
PostgreSQLHandler). All methods are async — called from AuditRail hooks which
are already async.

Env vars (same as observability server):
  OBSERVABILITY_DB_TYPE / OBSERVABILITY_SQLITE_PATH /
  OBSERVABILITY_DB_HOST / OBSERVABILITY_DB_PORT /
  OBSERVABILITY_DB_USER / OBSERVABILITY_DB_PASSWORD / OBSERVABILITY_DB_NAME /
  OBSERVABILITY_PG_SCHEMA
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.foundation.db.mysql_handler import MySQLHandler
from openjiuwen_runtime.foundation.db.postgresql_handler import PostgreSQLHandler

from jiuwenclaw_observability.schema import AUDIT_RULES_TABLE, DEFAULT_RULES

logger = logging.getLogger(__name__)

_handler = None
_init_lock = threading.Lock()


def _create_handler():
    """Create DB handler using same classes as manager."""
    db_type = os.getenv("OBSERVABILITY_DB_TYPE", "sqlite").strip().lower()

    if db_type == "sqlite":
        raw = os.getenv("OBSERVABILITY_SQLITE_PATH", "").strip()
        if not raw:
            repo_root = Path(__file__).resolve().parents[3]
            raw = str(repo_root / "observability" / "observability.db")
        p = Path(raw)
        if not p.is_absolute():
            repo_root = Path(__file__).resolve().parents[3]
            p = (repo_root / raw).resolve()
        return SQLiteHandler(str(p.as_posix()))

    elif db_type == "mysql":
        return MySQLHandler(
            host=os.getenv("OBSERVABILITY_DB_HOST", "127.0.0.1"),
            port=int(os.getenv("OBSERVABILITY_DB_PORT", "3306")),
            user=os.getenv("OBSERVABILITY_DB_USER", "root"),
            password=os.getenv("OBSERVABILITY_DB_PASSWORD", ""),
            database=os.getenv("OBSERVABILITY_DB_NAME", "observability"),
        )

    elif db_type in ("postgresql", "postgres", "pg"):
        return PostgreSQLHandler(
            host=os.getenv("OBSERVABILITY_DB_HOST", "127.0.0.1"),
            port=int(os.getenv("OBSERVABILITY_DB_PORT", "5432")),
            user=os.getenv("OBSERVABILITY_DB_USER", "root"),
            password=os.getenv("OBSERVABILITY_DB_PASSWORD", ""),
            database=os.getenv("OBSERVABILITY_DB_NAME", "observability"),
            schema=os.getenv("OBSERVABILITY_PG_SCHEMA", "public"),
        )

    else:
        raise ValueError(f"Unsupported db_type: {db_type}")


async def _get_handler():
    """Get or create + connect the DB handler. Ensures database and table exist."""
    global _handler
    if _handler is not None:
        return _handler
    handler = _create_handler()
    await handler.init_database()
    await handler.connect()
    await handler.init_table(AUDIT_RULES_TABLE)
    # Seed default rules if table is empty
    existing = await handler.list_records("audit_rules", limit=1)
    if not existing:
        now = datetime.now(tz=ZoneInfo(os.getenv("TZ", "UTC")))
        for rule in DEFAULT_RULES:
            try:
                await handler.create("audit_rules", {**rule, "created_at": now, "updated_at": now})
            except Exception as exc:
                logger.warning("[rule_loader] failed to seed rule %s: %s", rule.get("rule_name", ""), exc)
        logger.info("[rule_loader] Seeded %d default audit rules", len(DEFAULT_RULES))
    _handler = handler
    return _handler


async def get_rules_for_detector(detector: str) -> list[dict[str, Any]]:
    """Return enabled rules for a detector."""
    try:
        handler = await _get_handler()
        engine = handler.get_engine()
        async with engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT * FROM audit_rules WHERE detector = :d AND enabled = 1 ORDER BY id"),
                {"d": detector},
            )
            return [dict(r) for r in rows.mappings().all()]
    except Exception as e:
        logger.warning("[rule_loader] get_rules_for_detector failed: %s", e)
        return []


async def get_last_updated() -> str:
    """Return MAX(updated_at) from audit_rules, or '' if table doesn't exist."""
    try:
        handler = await _get_handler()
        engine = handler.get_engine()
        async with engine.connect() as conn:
            row = await conn.execute(text("SELECT MAX(updated_at) AS ts FROM audit_rules"))
            result = row.mappings().first()
            return str(result["ts"]) if result and result["ts"] else ""
    except Exception as e:
        logger.warning("[rule_loader] get_last_updated failed: %s", e)
        return ""
