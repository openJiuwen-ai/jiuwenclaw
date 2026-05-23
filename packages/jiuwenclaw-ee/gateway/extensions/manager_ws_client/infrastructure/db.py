# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""通用 ``DBHandler`` 生命周期（由 ``Settings`` / 环境变量驱动）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.mysql_handler import MySQLHandler
from openjiuwen_runtime.foundation.db.postgresql_handler import PostgreSQLHandler
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.foundation.log import get_logger

from ..models.table_init import init_all_tables
from .config import Settings, get_settings

logger = get_logger(__name__)


class Database:
    """数据库连接：解析路径、创建 ``DBHandler``、初始化并连接（幂等）。"""

    def __init__(
        self,
        cfg: Settings | None = None,
        *,
        relative_root: Path | None = None,
    ) -> None:
        self._cfg = cfg
        self._relative_root = relative_root
        self._handler: DBHandler | None = None
        self.tables_registered = False

    @property
    def settings(self) -> Settings:
        return self._cfg or get_settings()

    @property
    def handler(self) -> DBHandler:
        if self._handler is None:
            raise RuntimeError(
                "Database handler is not initialized; call ensure_ready first."
            )
        return self._handler

    def resolve_sqlite_path(self) -> Path:
        cfg = self.settings
        raw_path = Path(cfg.gateway_sqlite_path.strip()).expanduser()
        if raw_path.is_absolute():
            return raw_path.resolve()

        data_dir = os.getenv("JIUWENCLAW_DATA_DIR", "").strip()
        if data_dir:
            return (Path(data_dir) / raw_path).resolve()

        if self._relative_root is not None:
            return (self._relative_root / raw_path).resolve()

        return raw_path.resolve()

    def config_summary(self) -> dict[str, Any]:
        cfg = self.settings
        db_type = str(cfg.gateway_db_type or "").strip().lower() or "sqlite"
        if db_type == "sqlite":
            return {"db_type": db_type, "sqlite_path": str(self.resolve_sqlite_path())}
        return {
            "db_type": db_type,
            "host": cfg.gateway_db_host,
            "port": cfg.gateway_db_port,
            "database": cfg.gateway_db_name,
        }

    def _create_sqlite_handler(self) -> SQLiteHandler:
        db_path = self.resolve_sqlite_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return SQLiteHandler(str(db_path.as_posix()))

    def _create_mysql_handler(self) -> MySQLHandler:
        cfg = self.settings
        try:
            return MySQLHandler(
                host=str(cfg.gateway_db_host).strip(),
                port=int(cfg.gateway_db_port),
                user=str(cfg.gateway_db_user).strip(),
                password=str(cfg.gateway_db_password),
                database=str(cfg.gateway_db_name).strip(),
            )
        except (TypeError, ValueError) as e:
            logger.exception("Invalid MySQL database configuration.")
            raise ValueError("Invalid MySQL database configuration.") from e

    def create_handler(self) -> DBHandler:
        """根据配置创建 ``DBHandler`` 并缓存在本实例上。"""
        if self._handler is not None:
            return self._handler

        cfg = self.settings
        db_type = str(cfg.gateway_db_type or "").strip().lower() or "sqlite"
        logger.info("Using database: %s", db_type)

        if db_type == "sqlite":
            self._handler = self._create_sqlite_handler()
        elif db_type == "mysql":
            self._handler = self._create_mysql_handler()
        else:
            raise ValueError(f"Unsupported db_type: {db_type}. Use 'sqlite' or 'mysql'.")

        return self._handler

    async def ensure_ready(
        self,
        *,
        log_prefix: str = "",
    ) -> DBHandler:
        """连接数据库并注册 Gateway 表定义（进程内幂等）。"""
        if self._handler is not None:
            return self._handler

        handler = self.create_handler()
        await handler.init_database()
        await handler.connect()

        if not self.tables_registered:
            await init_all_tables(handler)
            self.tables_registered = True

        prefix = f"[{log_prefix}] " if log_prefix else ""
        logger.info("%sdatabase handler ready: %s", prefix, self.config_summary())
        return handler
