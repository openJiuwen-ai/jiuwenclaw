# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import logging
from typing import Any, Dict, List

from sqlalchemy import insert, update, select, delete, Table, MetaData, and_, or_, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error

from openjiuwen.core.foundation.store import BaseDbStore
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType



class SqlDbStore:
    def __init__(self, db_store: BaseDbStore):
        self.db_store = db_store
        self._async_table_cache: dict[str, Table] = {}
        self.async_session = async_sessionmaker(
            bind=self.db_store.get_async_engine(),
            expire_on_commit=False,
            class_=AsyncSession)

    async def write(self, table: str, data: dict) -> bool:
        t = await self.get_table(table)
        stmt = insert(t).values(**data)
        try:
            async with self.async_session() as session:
                async with session.begin():
                    await session.execute(stmt)
                return True
        except Exception as e:
            memory_logger.error(
                "Write failed",
                event_type=LogEventType.MEMORY_STORE,
                exception=str(e),
                metadata={"table_name": table}
            )
            return False

    async def get(self, table: str, record_id: str, columns: list[str] | None = None) -> dict[str, Any] | None:
        if columns is None:
            columns = []
        try:
            t = await self.get_table(table)
            if columns:
                cols = [t.c[col] for col in columns]
                stmt = select(*cols)
            else:
                stmt = select(t)
            stmt = stmt.where(t.c.id == record_id)
            async with self.async_session() as session:
                async with session.begin():
                    execute_result = await session.execute(stmt)
                    row = execute_result.mappings().first()
                    return dict(row) if row else None
        except Exception as e:
            memory_logger.error(
                "Failed to get data",
                event_type=LogEventType.MEMORY_RETRIEVE,
                exception=str(e),
                metadata={"table_name": table, "record_id": record_id}
            )
            return None

    async def get_with_sort(self, table: str, filters: Dict[str, Any], sort_by: str = "timestamp",
                             order: str = "ASC", limit: int = 100) -> List[Dict[str, Any]]:
        try:
            t = await self.get_table(table)
            if sort_by not in t.c:
                raise build_error(
                    StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                    memory_type="message",
                    error_msg=f"sort column '{sort_by}' does not exist "
                              f"in db store table '{table}'"
                )
            clauses = [
                t.c[col] == val for col, val in filters.items() if col in t.c
            ]
            stmt = select(t)
            if clauses:
                stmt = stmt.where(and_(*clauses))
            if order.upper() == "DESC":
                stmt = stmt.order_by(desc(t.c[sort_by]))
            else:
                stmt = stmt.order_by(asc(t.c[sort_by]))
            stmt = stmt.limit(limit)
            async with self.async_session() as session:
                async with session.begin():
                    execute_result = await session.execute(stmt)
                    result = execute_result.mappings().fetchall()
                    return [dict(row) for row in result]
        except Exception as e:
            memory_logger.error(
                "Failed to fetch filtered and sorted data",
                event_type=LogEventType.MEMORY_RETRIEVE,
                exception=str(e),
                metadata={"table_name": table}
            )
            return []

    async def exist(self, table: str, conditions: Dict[str, Any]) -> bool:
        t = await self.get_table(table)
        clauses = [t.c[col] == val for col, val in conditions.items()]
        stmt = select(1).where(and_(*clauses))
        async with self.async_session() as session:
            async with session.begin():
                execute_result = await session.execute(stmt)
                return execute_result.first() is not None

    async def batch_get(self, table: str, conditions_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        t = await self.get_table(table)
        clauses = [or_(*[t.c[col] == val for col, val in cond.items()]) for cond in conditions_list]
        stmt = select(t).where(or_(*clauses)) if clauses else select(t)
        async with self.async_session() as session:
            async with session.begin():
                execute_result = await session.execute(stmt)
                return [dict(r) for r in execute_result.mappings().fetchall()]

    async def condition_get(self, table: str, conditions: Dict[str, List[Any]],
                             columns: List[str] | None = None) -> List[Dict[str, Any]] | None:
        if columns is None:
            columns = []
        try:
            t: Table = await self.get_table(table)
            stmt = (
                select(t) if not columns
                else select(*[t.c[col] for col in columns])
            )
            clause_list = []
            for col, values in conditions.items():
                if not isinstance(values, list):
                    raise build_error(
                        StatusCode.MEMORY_GET_MEMORY_EXECUTION_ERROR,
                        memory_type="message",
                        error_msg=f"db store condition[{col}] must be a list, "
                                  f"(got {type(conditions[col]).__name__})",
                    )
                clause_list.append(t.c[col].in_(values))
            if clause_list:
                stmt = stmt.where(and_(*clause_list))
            async with self.async_session() as session:
                async with session.begin():
                    execute_result = await session.execute(stmt)
                    rows = execute_result.mappings().fetchall()
                    return [dict(r) for r in rows]
        except Exception as e:
            memory_logger.error(
                "Failed to get data via condition_get",
                event_type=LogEventType.MEMORY_RETRIEVE,
                exception=str(e),
                metadata={"table_name": table}
            )
            return None

    async def update(self, table: str, conditions: dict, data: dict) -> bool:
        t = await self.get_table(table)
        clauses = [t.c[col].in_(vals) if isinstance(vals, list) else t.c[col] == vals
                   for col, vals in conditions.items()]
        stmt = update(t).where(and_(*clauses)).values(**data)
        try:
            async with self.async_session() as session:
                async with session.begin():
                    await session.execute(stmt)
            return True
        except Exception as e:
            memory_logger.error(
                "Update failed",
                event_type=LogEventType.MEMORY_UPDATE,
                exception=str(e),
                metadata={"table_name": table}
            )
            return False

    async def delete(self, table: str, conditions: dict) -> bool:
        t = await self.get_table(table)
        clauses = [t.c[col].in_(vals) if isinstance(vals, list) else t.c[col] == vals
                   for col, vals in conditions.items()]
        stmt = delete(t).where(and_(*clauses))
        try:
            async with self.async_session() as session:
                async with session.begin():
                    await session.execute(stmt)
            return True
        except Exception as e:
            memory_logger.error(
                "Delete failed",
                event_type=LogEventType.MEMORY_DELETE,
                exception=str(e),
                metadata={"table_name": table}
            )
            return False

    async def delete_table(self, table_name: str) -> bool:
        try:
            metadata = MetaData()
            t = Table(table_name, metadata)
            async with self.db_store.get_async_engine().begin() as conn:
                await conn.run_sync(t.drop, checkfirst=True)
            return True
        except Exception as e:
            memory_logger.error(
                "Delete table failed",
                event_type=LogEventType.MEMORY_DELETE,
                exception=str(e),
                metadata={"table_name": table_name}
            )
            return False

    async def get_table(self, table_name: str) -> Table:
        if table_name in self._async_table_cache:
            return self._async_table_cache[table_name]
        metadata = MetaData()
        async with self.db_store.get_async_engine().connect() as conn:
            def sync_reflect(sync_conn):
                return Table(table_name, metadata, autoload_with=sync_conn)

            table = await conn.run_sync(sync_reflect)
            self._async_table_cache[table_name] = table
            return table
