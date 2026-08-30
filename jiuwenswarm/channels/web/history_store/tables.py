# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved

"""Web 会话历史表定义（sessions / messages）。

供 mysql/pg 经 foundation handler ``init_table`` 注册 ORM model，使高层 CRUD
(``list_records`` / ``get`` / ``create`` / ``update``) 可用。

仅在 Web 历史库走 mysql/pg（企业版）时由 ``db_actor`` 惰性 import；
个人版（纯内存）不会加载本模块，故不引入 foundation 依赖。
"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

# 时间戳列用 float（DOUBLE / REAL epoch 秒），与现有数据模型一致。
# content 用 mediumtext：mysql → MEDIUMTEXT(16MB)，pg → TEXT(无界)。

SESSIONS_TABLE_DEF = TableDefinition(
    table_name="sessions",
    columns=[
        ColumnDefinition("session_id", "string", primary_key=True, nullable=False),
        ColumnDefinition("user", "string", nullable=False, default="guest"),
        ColumnDefinition("title", "string", nullable=True),
        ColumnDefinition("message_count", "integer", nullable=False, default=0),
        ColumnDefinition("last_preview", "string", nullable=True),
        ColumnDefinition("created_at", "float", nullable=False),
        ColumnDefinition("updated_at", "float", nullable=False),
    ],
    indexes=[
        IndexDefinition(["user", "updated_at"], unique=False),
    ],
)

MESSAGES_TABLE_DEF = TableDefinition(
    table_name="messages",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, nullable=False, autoincrement=True),
        ColumnDefinition("session_id", "string", nullable=False),
        ColumnDefinition("request_id", "string", nullable=False),
        ColumnDefinition("role", "string", nullable=False),
        ColumnDefinition("content", "mediumtext", nullable=False),
        ColumnDefinition("event_type", "string", nullable=True),
        ColumnDefinition("timestamp", "float", nullable=False),
    ],
    indexes=[
        IndexDefinition(["session_id", "request_id", "role"], unique=True),
        IndexDefinition(["session_id", "timestamp"], unique=False),
    ],
)

ALL_TABLE_DEFINITIONS = (SESSIONS_TABLE_DEF, MESSAGES_TABLE_DEF)


async def init_web_history_tables(handler) -> None:
    """在已连接的 handler 上注册 Web 历史表（幂等：表已存在则跳过创建）。"""
    for table_def in ALL_TABLE_DEFINITIONS:
        await handler.init_table(table_def)
