"""身份库表初始化（幂等）。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_identity.models.identity_models import IDENTITY_TABLE_DEFINITIONS


async def init_all_tables(handler: DBHandler) -> None:
    for table_def in IDENTITY_TABLE_DEFINITIONS:
        await handler.init_table(table_def)
