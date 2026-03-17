# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from sqlalchemy import inspect, Column, String, insert, delete
from sqlalchemy.orm import declarative_mixin, declarative_base
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.foundation.store.base_db_store import BaseDbStore
from openjiuwen.core.memory.migration.migration_plan import sql_registry


Base = declarative_base()


@declarative_mixin
class MessageMixin:
    message_id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False)
    scope_id = Column(String(64), nullable=False)
    content = Column(String(4096), nullable=False)
    session_id = Column(String(64), nullable=True)
    role = Column(String(32), nullable=True)
    timestamp = Column(String(32), nullable=True)


@declarative_mixin
class ScopeUserMixin:
    user_id = Column(String(64), nullable=False, primary_key=True)
    scope_id = Column(String(64), nullable=False, primary_key=True)


@declarative_mixin
class MemoryMetaMixin:
    table_name = Column(String(64), nullable=False, primary_key=True)
    schema_version = Column(String(64), nullable=False)


class UserMessage(MessageMixin, Base):
    __tablename__ = "user_message"


class ScopeUserMapping(ScopeUserMixin, Base):
    __tablename__ = "scope_user_mapping"


class MemoryMeta(MemoryMetaMixin, Base):
    __tablename__ = "memory_meta"


# Configuration for memory tables with migration information
MEMORY_TABLES_CONFIG = [
    {
        "table": UserMessage.__table__,
        "entity_key": "user_messages"
    },
    {
        "table": ScopeUserMapping.__table__,
        "entity_key": "scope_user_mapping"
    }
]


async def create_tables(
    db_store: BaseDbStore,
):
    async with db_store.get_async_engine().begin() as conn:
        newly_created_tables = []

        def check_and_create(sync_conn):
            inspector = inspect(sync_conn)
            table_name = UserMessage.__tablename__

            if inspector.has_table(table_name):
                columns = inspector.get_columns(table_name)
                column_names = [col['name'] for col in columns]

                if 'group_id' in column_names:
                    UserMessage.__table__.drop(sync_conn, checkfirst=True)
                    memory_logger.debug(f"delete old version sql table")

            for table_config in MEMORY_TABLES_CONFIG:
                if not inspector.has_table(table_config["table"].name):
                    newly_created_tables.append(table_config["table"].name)

            Base.metadata.create_all(
                sync_conn,
                tables=[
                    MemoryMeta.__table__,
                    UserMessage.__table__,
                    ScopeUserMapping.__table__
                ],
                checkfirst=True
            )

        await conn.run_sync(check_and_create)

        def update_schema_versions(sync_conn):
            inspector = inspect(sync_conn)
            
            for table_config in MEMORY_TABLES_CONFIG:
                table_name = table_config["table"].name
                entity_key = table_config["entity_key"]
                if table_name in newly_created_tables:
                    current_version = sql_registry.get_current_version(entity_key)
                    if current_version > 0:
                        insert_stmt = insert(MemoryMeta.__table__).values(
                            table_name=table_name,
                            schema_version=str(current_version)
                        )
                        sync_conn.execute(insert_stmt)

        await conn.run_sync(update_schema_versions)
