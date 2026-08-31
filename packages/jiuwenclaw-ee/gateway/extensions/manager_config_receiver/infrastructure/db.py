# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""通用 ``DBHandler`` 生命周期（由 ``Settings`` / 环境变量驱动）。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.mysql_handler import MySQLHandler
from openjiuwen_runtime.foundation.db import postgresql_handler
from openjiuwen_runtime.foundation.db.postgresql_handler import PostgreSQLHandler
from openjiuwen_runtime.foundation.db.sqlite_handler import SQLiteHandler
from openjiuwen_runtime.foundation.db.utils import is_mysql, is_postgresql, is_sqlite
from openjiuwen_runtime.foundation.log import get_logger

from jiuwenswarm.gateway.config.enterprise.tables.table_init import init_all_tables
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
        self._ready_lock = asyncio.Lock()

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
        if is_sqlite(db_type):
            result = {
                "db_type": db_type,
                "sqlite_path": str(self.resolve_sqlite_path()),
            }
        else:
            result = {
                "db_type": db_type,
                "host": cfg.gateway_db_host,
                "port": cfg.gateway_db_port,
                "database": cfg.gateway_db_name,
            }
        if is_postgresql(db_type):
            result["schema"] = cfg.gateway_pg_schema
        return result

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

    def _create_pg_handler(self) -> PostgreSQLHandler:
        cfg = self.settings
        try:
            return PostgreSQLHandler(
                host=str(cfg.gateway_db_host).strip(),
                port=int(cfg.gateway_db_port),
                database=str(cfg.gateway_db_name).strip(),
                schema=str(cfg.gateway_pg_schema).strip(),
                user=str(cfg.gateway_db_user).strip(),
                password=str(cfg.gateway_db_password),
            )
        except (TypeError, ValueError) as e:
            logger.exception("Invalid PostgreSQL database configuration.")
            raise ValueError("Invalid PostgreSQL database configuration.") from e

    def create_handler(self) -> DBHandler:
        """根据配置创建 ``DBHandler`` 并缓存在本实例上。"""
        if self._handler is not None:
            return self._handler

        cfg = self.settings
        db_type = str(cfg.gateway_db_type or "").strip().lower() or "sqlite"
        logger.info("Using database: %s", db_type)

        if is_sqlite(db_type):
            self._handler = self._create_sqlite_handler()
        elif is_mysql(db_type):
            self._handler = self._create_mysql_handler()
        elif is_postgresql(db_type):
            self._handler = self._create_pg_handler()
        else:
            raise ValueError(
                f"Unsupported db_type: {db_type}. Use 'sqlite', 'mysql' or 'postgresql'."
            )

        return self._handler

    async def ensure_ready(
        self,
        *,
        log_prefix: str = "",
    ) -> DBHandler:
        """连接数据库并注册 Gateway 表定义（进程内幂等、并发安全）。"""
        if self.tables_registered and self._handler is not None:
            return self._handler

        async with self._ready_lock:
            if self.tables_registered and self._handler is not None:
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

    async def close(self) -> None:
        """断开连接并释放 handler（CLI / 短生命周期脚本应在 event loop 关闭前调用）。"""
        if self._handler is None:
            return
        try:
            await self._handler.disconnect()
        except Exception as exc:
            logger.warning("database disconnect error: %s", exc)
        finally:
            self._handler = None
            self.tables_registered = False


async def ensure_db_handler(*, log_prefix: str = "manager_config_receiver") -> DBHandler:
    """获取 DB CRUD 入口（遗留调用方，如 Agent ``installed_skill``）。

    - 已 ``wire_manager_ws_table_store``：返回 ``PersistentStore`` 适配器。
    - 未注入：回退 ``GatewayDb`` 直连（Agent 进程等无装配 PersistentStore 的场景）。

    **Gateway Manager Config Receiver 写路径走** ``require_*_repository`` /
    ``ensure_table_store``，勿依赖本函数的 GatewayDb 回退。
    """
    from .table_store_access import get_table_store_handler_if_wired

    wired = await get_table_store_handler_if_wired()
    if wired is not None:
        return wired  # type: ignore[return-value]
    logger.warning(
        "[%s] PersistentStore not wired; falling back to GatewayDb "
        "(legacy / Agent path; Manager routers must use ensure_table_store)",
        log_prefix,
    )
    return await get_shared_gateway_database().ensure_ready(log_prefix=log_prefix)


def get_shared_gateway_database() -> Database:
    """进程内唯一的 Gateway 本地库（``GatewayDb`` 单例）。"""
    from ..core.enterprise_config.gateway_db import GatewayDb

    return GatewayDb.current()
