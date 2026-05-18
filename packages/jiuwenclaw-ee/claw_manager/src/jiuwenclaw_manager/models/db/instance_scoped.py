"""组网实例内与 Gateway 对齐的复合主键 (jiuwenclaw_id, id)。"""

from __future__ import annotations

from sqlalchemy import PrimaryKeyConstraint

INSTANCE_SCOPED_PK = PrimaryKeyConstraint("jiuwenclaw_id", "id")
