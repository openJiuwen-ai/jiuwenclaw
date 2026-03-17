# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any
from openjiuwen.core.memory.manage.mem_model.sql_db_store import SqlDbStore


class MemoryMetaManager:
    def __init__(self,
                 sql_db_store: SqlDbStore):
        self.sql_db = sql_db_store
        self.meta_table = "memory_meta"

    async def add(self, table_name: str, schema_version: str, **kwargs):
        if not table_name or not schema_version:
            return
        data = {
            'table_name': table_name,
            'schema_version': schema_version,
        }
        exists = await self.sql_db.exist(
            table=self.meta_table,
            conditions={
                "table_name": data["table_name"],
                "schema_version": data["schema_version"],
            },
        )
        if exists:
            return
        await self.sql_db.write(self.meta_table, data)

    async def delete_by_table_name(self, table_name: str) -> bool:
        return await self.sql_db.delete(
            table=self.meta_table,
            conditions={"table_name": table_name}
        )

    async def get_by_table_name(self, table_name: str) -> list[dict[str, Any]] | None:
        results = await self.sql_db.condition_get(
            table=self.meta_table,
            conditions={"table_name": [table_name]},
            columns=None
        )
        return results if results else None
