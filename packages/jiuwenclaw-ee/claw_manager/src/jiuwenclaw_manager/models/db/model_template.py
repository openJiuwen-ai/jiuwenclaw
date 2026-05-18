"""模型模板表 model_template（与企业级 claw 数据模型设计对齐）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from jiuwenclaw_manager.models.db.base import Base
from jiuwenclaw_manager.models.db.instance_scoped import INSTANCE_SCOPED_PK


class ModelTemplate(Base):
    __tablename__ = "model_template"
    __table_args__ = (INSTANCE_SCOPED_PK,)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    jiuwenclaw_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("instance_info.jiuwenclaw_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)
    model_type: Mapped[Any] = mapped_column(JSON, nullable=False)
    model_tags: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    api_base: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    timeout: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="3")
    enable_streaming: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="1")
    enable_function_calling: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="1")
    verify_ssl: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="1")
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
