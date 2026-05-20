# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""PostgreSQL 数据库句柄，继承 SQLAlchemyHandler，使用 asyncpg 驱动。"""

from __future__ import annotations

from urllib.parse import quote_plus

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from openjiuwen_runtime.foundation.db.sqlalchemy_handler import SQLAlchemyHandler
from openjiuwen_runtime.foundation.db.table_def import ColumnDefinition
from openjiuwen_runtime.foundation.log import get_logger

logger = get_logger(__name__)


class PostgreSQLHandler(SQLAlchemyHandler):
    """PostgreSQL 数据库句柄。

    连接参数通过构造函数显式传入，拼装 ``postgresql+asyncpg://`` URL
    后委托给 :class:`SQLAlchemyHandler` 基类。
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5432,
        database: str = "claw_manager",
        user: str = "postgres",
        password: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password

        database_url = (
            f"postgresql+asyncpg://{quote_plus(user)}:{quote_plus(password)}"
            f"@{host}:{port}/{database}"
        )
        super().__init__(database_url)

    async def init_database(self) -> None:
        """确保目标数据库存在（不存在则创建）。

        PostgreSQL 不支持 ``CREATE DATABASE IF NOT EXISTS``，
        因此先连 ``postgres`` 默认库查询 ``pg_database``。
        注意 CREATE DATABASE 不能在事务中执行，需 AUTOCOMMIT。
        """
        db_name = (self.database or "").strip()
        if not db_name:
            logger.warning("No database name configured, skipping init_database")
            return

        url = make_url(self.database_url)
        server_url = url.set(database="postgres")
        temp_engine = create_async_engine(
            server_url.render_as_string(hide_password=False),
            echo=False,
            isolation_level="AUTOCOMMIT",
        )
        try:
            async with temp_engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": db_name},
                )
                if result.scalar() is None:
                    quoted = db_name.replace('"', '""')
                    await conn.execute(text(f'CREATE DATABASE "{quoted}"'))
                    logger.info("PostgreSQL database created: database=%s", db_name)
                else:
                    logger.debug("PostgreSQL database already exists: database=%s", db_name)
        finally:
            await temp_engine.dispose()

    def _get_column_sql_type(self, col_def: ColumnDefinition) -> str:
        """PostgreSQL 方言类型映射。

        基类将 datetime 映射为 DATETIME（MySQL 语法），PG 需改为 TIMESTAMP。
        此方法仅在 ALTER TABLE ADD COLUMN（增量同步缺失列）时调用。
        """
        if col_def.data_type.lower() == "datetime":
            return "TIMESTAMP"
        return super()._get_column_sql_type(col_def)
