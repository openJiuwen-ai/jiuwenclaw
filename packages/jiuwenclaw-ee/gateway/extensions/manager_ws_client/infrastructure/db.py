# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Manager WS Client 数据库句柄；配置来自 .env / 环境变量（见 .env.example）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.mysql_handler import MySQLHandler
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.foundation.log import get_logger

from .config import Settings, get_settings

_EXT_ROOT = Path(__file__).resolve().parents[1]

logger = get_logger(__name__)

_db_handler: DBHandler | None = None


def get_db_handler() -> DBHandler:
    if _db_handler is None:
        raise RuntimeError("Database handler is not initialized; call ensure_db_handler_ready first.")
    return _db_handler


def _resolve_sqlite_path(cfg: Settings) -> Path:
    raw_path = Path(cfg.sqlite_path.strip()).expanduser()
    if raw_path.is_absolute():
        return raw_path.resolve()

    data_dir = os.getenv("JIUWENCLAW_DATA_DIR", "").strip()
    if data_dir:
        return (Path(data_dir) / raw_path).resolve()

    return (_EXT_ROOT / raw_path).resolve()


def _sqlite_handler_from_settings(cfg: Settings) -> SQLiteHandler:
    db_path = _resolve_sqlite_path(cfg)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteHandler(str(db_path.as_posix()))


def _mysql_handler_from_settings(cfg: Settings) -> MySQLHandler:
    try:
        return MySQLHandler(
            host=str(cfg.db_host).strip(),
            port=int(cfg.db_port),
            user=str(cfg.db_user).strip(),
            password=str(cfg.db_password),
            database=str(cfg.db_name).strip(),
        )
    except (TypeError, ValueError) as e:
        logger.exception(
            "Invalid MySQL database configuration "
            "(MANAGER_WS_CLIENT_DB_HOST/PORT/USER/PASSWORD/NAME)."
        )
        raise ValueError("Invalid MySQL database configuration.") from e


def create_db_handler(cfg: Settings | None = None) -> DBHandler:
    """根据 ``Settings`` / ``.env`` 创建并注册全局 ``DBHandler``。"""
    global _db_handler

    active = cfg or get_settings()
    db_type = str(active.db_type or "").strip().lower() or "sqlite"
    logger.info("Using database: %s", db_type)

    if db_type == "sqlite":
        _db_handler = _sqlite_handler_from_settings(active)
    elif db_type == "mysql":
        _db_handler = _mysql_handler_from_settings(active)
    else:
        raise ValueError(f"Unsupported db_type: {db_type}. Use 'sqlite' or 'mysql'.")

    return _db_handler


def database_config_summary(cfg: Settings | None = None) -> dict[str, Any]:
    active = cfg or get_settings()
    db_type = str(active.db_type or "").strip().lower() or "sqlite"
    if db_type == "sqlite":
        return {"db_type": db_type, "sqlite_path": str(_resolve_sqlite_path(active))}
    return {
        "db_type": db_type,
        "host": active.db_host,
        "port": active.db_port,
        "database": active.db_name,
    }


async def ensure_db_handler_ready() -> DBHandler:
    """创建并连接 DB（幂等）。供 manager_ws_client/ws_client manager_ws_client_router 使用。"""
    global _db_handler

    if _db_handler is not None:
        return _db_handler

    handler = create_db_handler(get_settings())
    await handler.init_database()
    await handler.connect()
    from ..models.table_init import init_all_tables

    await init_all_tables(handler)
    logger.info(
        "[manager_ws_client] database handler ready: %s",
        database_config_summary(),
    )
    return handler
