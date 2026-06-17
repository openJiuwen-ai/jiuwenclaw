"""Gateway 模板引用索引表 ``jid_template_ref``（MDB）。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

JID_TEMPLATE_REF_TABLE_DEF = TableDefinition(
    table_name="jid_template_ref",
    columns=[
        ColumnDefinition(
            "jiuwenclaw_id",
            "string",
            length=64,
            primary_key=True,
            nullable=False,
        ),
        ColumnDefinition(
            "slot",
            "string",
            length=128,
            primary_key=True,
            nullable=False,
        ),
        ColumnDefinition(
            "template_id",
            "string",
            length=100,
            primary_key=True,
            nullable=False,
        ),
        ColumnDefinition("ref_count", "integer", nullable=False, default=0),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["jiuwenclaw_id"], unique=False),
        IndexDefinition(["template_id"], unique=False),
        IndexDefinition(["jiuwenclaw_id", "template_id"], unique=False),
    ],
)
