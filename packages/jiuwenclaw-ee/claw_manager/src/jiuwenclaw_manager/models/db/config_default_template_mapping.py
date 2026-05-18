"""用户与群组默认模板映射表 config_default_template_mapping。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from jiuwenclaw_manager.models.db.base import Base
from jiuwenclaw_manager.models.db.instance_scoped import INSTANCE_SCOPED_PK


class ConfigDefaultTemplateMapping(Base):
    __tablename__ = "config_default_template_mapping"
    __table_args__ = (INSTANCE_SCOPED_PK,)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    jiuwenclaw_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("instance_info.jiuwenclaw_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    user_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    group_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    template_id: Mapped[str] = mapped_column(String(512), nullable=False)
    template_type: Mapped[str] = mapped_column(String(512), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="1")
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
