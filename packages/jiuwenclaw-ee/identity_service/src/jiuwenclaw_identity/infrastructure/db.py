"""身份服务数据库句柄（SQLite / MySQL / PostgreSQL）；配置来自 .env（IDENTITY_*）。

独立身份库：与 claw_manager 的管理库物理隔离。底层走 foundation DBHandler 抽象，
先用 SQLite 保证功能，后续切 MySQL/PostgreSQL 仅改 .env，不动业务代码。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.mysql_handler import MySQLHandler
from openjiuwen_runtime.foundation.db.postgresql_handler import PostgreSQLHandler
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler

from jiuwenclaw_identity.infrastructure.config import Settings, settings
from jiuwenclaw_identity.infrastructure.logger import get_logger

_PKG_ROOT = Path(__file__).resolve().parents[3]

logger = get_logger(__name__)

_db_handler: DBHandler | None = None


def get_db_handler() -> DBHandler:
    if _db_handler is None:
        raise RuntimeError("Database handler is not initialized; call create_db_handler first.")
    return _db_handler


def _sqlite_handler_from_settings(cfg: Settings) -> SQLiteHandler:
    raw_path = Path(cfg.sqlite_path.strip()).expanduser()
    raw_path = raw_path.resolve() if raw_path.is_absolute() else (_PKG_ROOT / raw_path).resolve()
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteHandler(str(raw_path.as_posix()))


def _mysql_handler_from_settings(cfg: Settings) -> MySQLHandler:
    try:
        return MySQLHandler(
            host=str(cfg.db_host).strip(), port=int(cfg.db_port),
            user=str(cfg.db_user).strip(), password=str(cfg.db_password),
            database=str(cfg.db_name).strip(),
        )
    except (TypeError, ValueError) as e:
        logger.exception("Invalid MySQL config (AUTH_SRV_DB_*).")
        raise ValueError("Invalid MySQL database configuration.") from e


def _pg_handler_from_settings(cfg: Settings) -> PostgreSQLHandler:
    try:
        return PostgreSQLHandler(
            host=str(cfg.db_host).strip(), port=int(cfg.db_port),
            user=str(cfg.db_user).strip(), password=str(cfg.db_password),
            database=str(cfg.db_name).strip(), schema=str(cfg.pg_schema).strip(),
        )
    except (TypeError, ValueError) as e:
        logger.exception("Invalid PostgreSQL config (AUTH_SRV_DB_*).")
        raise ValueError("Invalid PostgreSQL database configuration.") from e


def create_db_handler(cfg: Settings | None = None) -> DBHandler:
    global _db_handler
    active = cfg or settings
    db_type = str(active.db_type or "").strip().lower() or "sqlite"
    logger.info("Using identity database: %s", db_type)
    if db_type == "sqlite":
        _db_handler = _sqlite_handler_from_settings(active)
    elif db_type == "mysql":
        _db_handler = _mysql_handler_from_settings(active)
    elif db_type in ("postgresql", "postgres", "pg"):
        _db_handler = _pg_handler_from_settings(active)
    else:
        raise ValueError(f"Unsupported db_type: {db_type}. Use sqlite/mysql/postgresql.")
    return _db_handler


def database_config_summary(cfg: Settings | None = None) -> dict[str, Any]:
    active = cfg or settings
    db_type = str(active.db_type or "").strip().lower() or "sqlite"
    if db_type == "sqlite":
        return {"db_type": db_type, "sqlite_path": active.sqlite_path}
    return {"db_type": db_type, "host": active.db_host, "port": active.db_port, "database": active.db_name}
