"""企业配置生效策略库句柄（风格对齐 manager_ws_client.infrastructure.db）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from jiuwenswarm.common.utils import logger

from .settings import EffectivePolicyDatabaseSettings, get_settings

_LOG = "[enterprise_config]"

_db_handler: Any | None = None


def _import_runtime_db():
    try:
        from openjiuwen_runtime.foundation.db.handler import DBHandler
        from openjiuwen_runtime.foundation.db.mysql_handler import MySQLHandler
        from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "openjiuwen_runtime is required for enterprise policy database access"
        ) from exc
    return DBHandler, MySQLHandler, SQLiteHandler


def get_db_handler() -> Any:
    if _db_handler is None:
        raise RuntimeError(
            "Database handler is not initialized; call ensure_db_handler_ready first."
        )
    return _db_handler


def _resolve_sqlite_path(cfg: EffectivePolicyDatabaseSettings) -> Path:
    if cfg.sqlite_path:
        raw_path = Path(cfg.sqlite_path).expanduser()
    else:
        raw_path = Path("gateway.db")
    if raw_path.is_absolute():
        return raw_path.resolve()
    data_dir = (
        os.getenv("JIUWENSWARM_DATA_DIR", "").strip()
        or os.getenv("JIUWENCLAW_DATA_DIR", "").strip()
    )
    if data_dir:
        return (Path(data_dir) / raw_path).resolve()
    return raw_path.resolve()


def _sqlite_handler_from_settings(cfg: EffectivePolicyDatabaseSettings) -> Any:
    _, _, SQLiteHandler = _import_runtime_db()
    db_path = _resolve_sqlite_path(cfg)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteHandler(str(db_path.as_posix()))


def _mysql_handler_from_settings(cfg: EffectivePolicyDatabaseSettings) -> Any:
    _, MySQLHandler, _ = _import_runtime_db()
    try:
        return MySQLHandler(
            host=str(cfg.mysql_host).strip(),
            port=int(cfg.mysql_port),
            user=str(cfg.mysql_user).strip(),
            password=str(cfg.mysql_password),
            database=str(cfg.mysql_database).strip(),
        )
    except (TypeError, ValueError) as exc:
        logger.exception("%s invalid MySQL database configuration", _LOG)
        raise ValueError("Invalid MySQL database configuration.") from exc


def create_db_handler(
    cfg: EffectivePolicyDatabaseSettings | None = None,
) -> Any:
    """根据配置创建并注册全局 ``DBHandler``。"""
    global _db_handler

    active = cfg or get_settings()
    db_type = str(active.db_type or "").strip().lower() or "mysql"
    logger.info("%s using database: %s", _LOG, db_type)

    if db_type == "sqlite":
        _db_handler = _sqlite_handler_from_settings(active)
    elif db_type == "mysql":
        _db_handler = _mysql_handler_from_settings(active)
    else:
        raise ValueError(f"Unsupported db_type: {db_type}. Use 'sqlite' or 'mysql'.")

    return _db_handler


def database_config_summary(
    cfg: EffectivePolicyDatabaseSettings | None = None,
) -> dict[str, Any]:
    active = cfg or get_settings()
    db_type = str(active.db_type or "").strip().lower() or "mysql"
    if db_type == "sqlite":
        return {"db_type": db_type, "sqlite_path": str(_resolve_sqlite_path(active))}
    return {
        "db_type": db_type,
        "host": active.mysql_host,
        "port": active.mysql_port,
        "database": active.mysql_database,
    }


async def ensure_db_handler_ready(
    cfg: EffectivePolicyDatabaseSettings | None = None,
) -> Any:
    """创建、连接并初始化策略表（幂等）。"""
    global _db_handler

    if _db_handler is not None:
        return _db_handler

    active = cfg or get_settings()
    handler = create_db_handler(active)
    await handler.init_database()
    await handler.connect()
    await _init_policy_tables(handler)
    logger.info("%s database handler ready: %s", _LOG, database_config_summary(active))
    return handler


def reset_db_handler() -> None:
    """清除句柄缓存（热更新后调用）。"""
    global _db_handler
    _db_handler = None


async def _init_policy_tables(handler: DBHandler) -> None:
    for table_def in _load_table_definitions():
        try:
            await handler.init_table(table_def)
        except Exception as exc:
            logger.warning(
                "%s init table %s failed: %s",
                _LOG,
                table_def.table_name,
                exc,
            )


def _load_table_definitions() -> list[Any]:
    """加载策略表定义；ee 包不在仓库内时跳过（表由外部/已有库提供）。"""
    repo_root = Path(__file__).resolve().parents[4]
    candidates = (
        repo_root
        / "packages"
        / "jiuwenswarm-ee"
        / "gateway"
        / "extensions"
        / "manager_ws_client",
        repo_root
        / "packages"
        / "jiuwenclaw-ee"
        / "gateway"
        / "extensions"
        / "manager_ws_client",
    )
    ext_root = next((p for p in candidates if (p / "models").is_dir()), None)
    if ext_root is None:
        logger.debug(
            "%s manager_ws_client table definitions not found; skip init_table",
            _LOG,
        )
        return []

    root_str = str(ext_root.resolve())
    inserted = root_str not in sys.path
    if inserted:
        sys.path.append(root_str)

    try:
        import importlib

        table_init = importlib.import_module("models.table_init")
        return list(table_init.ALL_TABLE_DEFINITIONS)
    except Exception as exc:
        logger.warning("%s load table definitions failed: %s", _LOG, exc)
        return []
    finally:
        if inserted:
            sys.path.remove(root_str)
