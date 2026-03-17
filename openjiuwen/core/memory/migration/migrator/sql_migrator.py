# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Any, Dict, List
from alembic.runtime.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text,\
    inspect, MetaData, Table, select, text, insert, update
from openjiuwen.core.memory.manage.mem_model.sql_db_store import SqlDbStore
from openjiuwen.core.memory.manage.mem_model.db_model import MEMORY_TABLES_CONFIG
from openjiuwen.core.memory.migration.migrator.memory_meta_manager import MemoryMetaManager
from openjiuwen.core.memory.migration.operation.base_operation import BaseOperation
from openjiuwen.core.memory.migration.operation.operations import AddColumnOperation,\
RenameColumnOperation, UpdateColumnTypeOperation
from openjiuwen.core.common.logging import memory_logger


class SQLMigrator:
    def __init__(self, sql_db_store: SqlDbStore):
        self.sql_db = sql_db_store
        self.memory_meta_manager = MemoryMetaManager(self.sql_db)
        self.engine = sql_db_store.db_store.get_async_engine()

    @staticmethod    
    def _validate_table(table_name: str):
        """
        Validate if the table is supported for migration
        
        Args:
            table_name: Table name to validate
            
        Raises:
            ValueError: If table is not supported
        """
        if table_name not in [table_config["table"].name for table_config in MEMORY_TABLES_CONFIG]:
            raise ValueError(f"Unsupported table name: {table_name}")

    @staticmethod
    def get_sqlalchemy_type(type_string: str):
        """Map string type to SQLAlchemy type"""
        type_map = {
            'STRING': String,
            'VARCHAR': String,
            'INTEGER': Integer,
            'INT': Integer,
            'DATETIME': DateTime,
            'BOOLEAN': Boolean,
            'BOOL': Boolean,
            'TEXT': Text,
            'FLOAT': Float,
        }

        # Extract base type and length parameters
        if '(' in type_string:
            base_type, params = type_string.split('(', 1)
            params = params.rstrip(')')
            base_type_upper = base_type.upper()
            if base_type_upper in ['STRING', 'VARCHAR']:
                return String(length=int(params))
            elif base_type_upper == 'TEXT':
                return Text()
        else:
            base_type_upper = type_string.upper()
            if base_type_upper in type_map:
                # STRING and VARCHAR require length specification, default to 255
                if base_type_upper in ['STRING', 'VARCHAR']:
                    return String(length=255)
                return type_map[base_type_upper]()

        # Default to Text type
        return Text()

    def _migrate_add_column(self, op, operation: AddColumnOperation, dialect_name: str, sync_conn):
        """
        Migrate add column operation
        
        Args:
            op: Alembic Operations object
            operation: AddColumnOperation instance
            dialect_name: Database dialect name
            sync_conn: SQLAlchemy synchronous connection
        """
        table_name = operation.table
        self._validate_table(table_name)
        # Use Alembic's add_column method
        column_type = self.get_sqlalchemy_type(operation.column_type)
        op.add_column(
            table_name,
            Column(
                operation.column_name,
                column_type,
                nullable=operation.nullable,
                default=operation.default
            )
        )
    
    def _migrate_rename_column(self, op, operation: RenameColumnOperation, dialect_name: str, sync_conn):
        """
        Migrate rename column operation
        
        Args:
            op: Alembic Operations object
            operation: RenameColumnOperation instance
            dialect_name: Database dialect name
            sync_conn: SQLAlchemy synchronous connection
        """
        table_name = operation.table
        self._validate_table(table_name)
        # MySQL requires column type specification
        if dialect_name == 'mysql':
            inspector = inspect(sync_conn)
            columns = inspector.get_columns(table_name)
            existing_type = None
            for col in columns:
                if col['name'] == operation.old_column_name:
                    existing_type = col['type']
                    break
            if existing_type:
                op.alter_column(
                    table_name,
                    operation.old_column_name,
                    new_column_name=operation.new_column_name,
                    type_=existing_type
                )
            else:
                op.alter_column(
                    table_name,
                    operation.old_column_name,
                    new_column_name=operation.new_column_name
                )
        else:
            # SQLite doesn't require type specification
            op.alter_column(
                table_name,
                operation.old_column_name,
                new_column_name=operation.new_column_name
            )
    
    def _migrate_update_column_type(self, op, operation: UpdateColumnTypeOperation, dialect_name: str, sync_conn):
        """
        Migrate update column type operation
        
        Args:
            op: Alembic Operations object
            operation: UpdateColumnTypeOperation instance
            dialect_name: Database dialect name
            sync_conn: SQLAlchemy synchronous connection
        """
        table_name = operation.table
        self._validate_table(table_name)
        # SQLite doesn't support ALTER COLUMN TYPE
        if dialect_name == 'sqlite':
            # For SQLite, use create new table and copy data approach
            self._alter_column_type_sqlite(
                sync_conn,
                table_name,
                operation.column_name,
                operation.new_column_type)
        else:
            new_column_type = self.get_sqlalchemy_type(operation.new_column_type)
            op.alter_column(
                table_name,
                operation.column_name,
                type_=new_column_type
            )
    
    async def try_migrate(self, entity_key: str, operations: List[BaseOperation]) -> bool:
        """
        Migrate table schema to target version

        Args:
            entity_key: Table name to migrate
            operations: List containing migration operations

        Returns:
            bool: Whether the migration was successful
        """

        if not operations:
            return True

        table_name = entity_key
        current_version = None

        try:
            # Get current version
            current_meta = await self.memory_meta_manager.get_by_table_name(table_name)
            if current_meta and len(current_meta) > 0:
                current_version = int(current_meta[0]['schema_version'])
            operations = [operation for operation in operations 
                          if current_version is None or operation.metadata.schema_version > current_version]

            # Define operation to method mapping
            operation_handlers = {
                AddColumnOperation: self._migrate_add_column,
                RenameColumnOperation: self._migrate_rename_column,
                UpdateColumnTypeOperation: self._migrate_update_column_type,
            }

            # Execute migration operations using Alembic
            async with self.engine.begin() as conn:
                def execute_operations(sync_conn):
                    # Create Alembic context
                    ctx = MigrationContext.configure(sync_conn)
                    op = Operations(ctx)
                    
                    # Detect database type
                    dialect_name = sync_conn.dialect.name

                    for operation in operations:
                        target_version = operation.metadata.schema_version
                        # Get the appropriate handler for this operation type
                        handler = operation_handlers.get(type(operation))
                        if handler:
                            # Call the handler with necessary parameters
                            handler(op, operation, dialect_name, sync_conn)
                        else:
                            raise ValueError(f"Unsupported operation type: {operation}")

                    # Update version information in memory_meta table
                    if operations:
                        target_version = str(operations[-1].metadata.schema_version)
                        # Update version record
                        metadata = MetaData()
                        memory_meta_table = Table('memory_meta', metadata, autoload_with=sync_conn)
                        
                        # Try to update first
                        update_stmt = update(memory_meta_table).where(
                            memory_meta_table.c.table_name == table_name
                        ).values(schema_version=target_version)
                        result = sync_conn.execute(update_stmt)
                        
                        # If no rows were updated, insert a new record
                        if result.rowcount == 0:
                            insert_stmt = insert(memory_meta_table).values(
                                table_name=table_name, 
                                schema_version=target_version
                            )
                            sync_conn.execute(insert_stmt)

                await conn.run_sync(execute_operations)

            return True

        except Exception as e:
            memory_logger.error(f"Error during migration of table {table_name}: {str(e)}")
            return False

    def _alter_column_type_sqlite(self, conn, table_name: str, column_name: str, new_column_type: str):
        """
        Alter column type in SQLite by creating a new table and copying data
        
        Args:
            conn: SQLAlchemy synchronous connection
            table_name: Table name
            column_name: Column name to modify
            new_column_type: New column type
        """
        try:
            # Get original table structure
            metadata = MetaData()
            old_table = Table(table_name, metadata, autoload_with=conn)
            
            # Check if column exists
            if column_name not in old_table.columns:
                raise ValueError(f"Column {column_name} not found in table {table_name}")
            
            # Create new table structure
            new_table_name = f"{table_name}_new_{column_name}"
            new_columns = []
            
            for col in old_table.columns:
                if col.name == column_name:
                    # Use new column type
                    new_col_type = self.get_sqlalchemy_type(new_column_type)
                    new_columns.append(Column(
                        col.name, 
                        new_col_type, 
                        nullable=col.nullable, 
                        default=col.default
                    ))
                else:
                    # Copy original column
                    new_columns.append(Column(
                        col.name, 
                        col.type, 
                        nullable=col.nullable, 
                        default=col.default
                    ))
            
            # Create new table
            new_table = Table(new_table_name, metadata, *new_columns)
            new_table.create(conn, checkfirst=True)
            
            # Copy data
            insert_stmt = new_table.insert().from_select(
                [col.name for col in old_table.columns],
                select(old_table)
            )
            conn.execute(insert_stmt)
            
            # Drop original table
            old_table.drop(conn)
            
            # Rename new table
            conn.execute(text(f"ALTER TABLE {new_table_name} RENAME TO {table_name}"))
            
            memory_logger.info(
                f"Successfully altered column type for {table_name}.{column_name} to {new_column_type}"
            )
            
        except Exception as e:
            memory_logger.error(
                f"Error altering column type in SQLite for table {table_name}, "
                f"column {column_name}: {str(e)}"
            )
            raise

    async def batch_migrate(self, migrations: List[Dict[str, Any]]) -> Dict[str, bool]:
        """
        Batch execute table schema migrations

        Args:
            migrations: List of migration tasks, each containing table_name and operations

        Returns:
            Dict[str, bool]: Migration result for each table
        """
        results = {}

        for migration in migrations:
            table_name = migration.get('table_name')
            operations = migration.get('operations', [])

            success = await self.try_migrate(entity_key=table_name, operations=operations)

            results[table_name] = success
        
        return results
