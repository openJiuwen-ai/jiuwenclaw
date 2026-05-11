# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Agent Client 数据库句柄（SQLiteHandler / MySQLHandler）；类型与连接信息来自 config.yaml。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.mysql_handler import MySQLHandler
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.foundation.log import get_logger

from jiuwenclaw.config import get_config
from jiuwenclaw.utils import get_user_workspace_dir

logger = get_logger(__name__)

_db_handler: DBHandler | None = None


def get_db_handler() -> DBHandler:
    if _db_handler is None:
        raise RuntimeError("Database handler is not initialized; call init_database first.")
    return _db_handler


def _sqlite_handler_from_db_cfg(db_cfg: dict[str, Any]) -> SQLiteHandler:
    sqlite_path = db_cfg.get("sqlite_path")
    if isinstance(sqlite_path, str) and sqlite_path.strip():
        raw_path = Path(sqlite_path.strip()).expanduser()
        db_path = str(raw_path.resolve().as_posix())
    else:
        gateway = get_user_workspace_dir() / "gateway"
        gateway.mkdir(parents=True, exist_ok=True)
        db_path = str((gateway / "agent_client.db").resolve().as_posix())
    return SQLiteHandler(db_path)


def _mysql_handler_from_db_cfg(db_cfg: dict[str, Any]) -> MySQLHandler:
    """从 ``database`` 配置构造 ``MySQLHandler``；``db`` 子节须含 host、port、user、password、db_name。

    配置缺失或类型无效时先记录完整异常再抛出 ``ValueError``。
    """
    try:
        conn = db_cfg["db"]
        return MySQLHandler(
            host=str(conn["host"]).strip(),
            port=int(conn["port"]),
            user=str(conn["user"]).strip(),
            password=str(conn["password"]),
            database=str(conn["db_name"]).strip(),
        )
    except (KeyError, TypeError, ValueError) as e:
        logger.exception(
            "Invalid or incomplete MySQL database configuration "
            "(extensions.agent_client_rest.database.db); "
            "expected keys host, port, user, password, db_name with compatible types."
        )
        raise ValueError(
            "Invalid MySQL database configuration (extensions.agent_client_rest.database.db)."
        ) from e


def init_database() -> DBHandler:
    """根据 ``config.yaml`` → ``extensions.agent_client_rest.database`` 初始化句柄；未配置 ``db_type`` 时默认 sqlite。"""
    global _db_handler

    cfg = get_config()
    db_cfg = (
        (cfg.get("extensions") or {})
        .get("agent_client_rest", {})
        .get("database", {})
    )
    raw_type = db_cfg.get("db_type")
    db_type = str(raw_type or "").strip().lower() or "sqlite"

    logger.info(f"Using database: {db_type}")

    if db_type == "sqlite":
        _db_handler = _sqlite_handler_from_db_cfg(db_cfg)

    elif db_type == "mysql":
        _db_handler = _mysql_handler_from_db_cfg(db_cfg)

    else:
        raise ValueError(
            f"Unsupported db_type: {db_type}. Use 'sqlite' or 'mysql'."
        )

    return _db_handler
