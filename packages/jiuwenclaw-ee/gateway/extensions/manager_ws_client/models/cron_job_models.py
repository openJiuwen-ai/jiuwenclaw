# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""企业级定时任务表 ``cron_job``（按 ``jiuwenclaw_id`` 实例隔离）。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

CRON_JOB_TABLE_DEF = TableDefinition(
    table_name="cron_job",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("job_id", "string", length=64, nullable=False),
        ColumnDefinition("group_id", "string", length=256, nullable=True),
        ColumnDefinition("bot_id", "string", length=256, nullable=True),
        ColumnDefinition("user_id", "string", length=256, nullable=True),
        ColumnDefinition("name", "string", length=256, nullable=False),
        ColumnDefinition("description", "string", length=4096, nullable=True),
        ColumnDefinition("cron_expr", "string", length=128, nullable=False),
        ColumnDefinition("timezone", "string", length=64, nullable=False),
        ColumnDefinition("wake_offset_seconds", "integer", nullable=False, default=300),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("expired", "boolean", nullable=False, default=False),
        ColumnDefinition("delete_after_run", "boolean", nullable=False, default=False),
        ColumnDefinition("mode", "string", length=32, nullable=False, default="agent"),
        ColumnDefinition("targets", "string", length=256, nullable=False),
        ColumnDefinition("session_id", "string", length=512, nullable=True),
        ColumnDefinition("chat_type", "string", length=32, nullable=True),
        ColumnDefinition("next_run_at", "datetime", nullable=True),
        ColumnDefinition("last_run_at", "datetime", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
        ColumnDefinition("data", "json", nullable=True),
    ],
    indexes=[
        IndexDefinition(["jiuwenclaw_id", "job_id"], unique=True),
        IndexDefinition(["jiuwenclaw_id", "group_id", "bot_id", "user_id"]),
        IndexDefinition(["jiuwenclaw_id", "group_id", "bot_id"]),
        IndexDefinition(["jiuwenclaw_id", "user_id"]),
        IndexDefinition(["jiuwenclaw_id", "enabled", "expired", "next_run_at"]),
    ],
)

__all__ = ("CRON_JOB_TABLE_DEF",)
