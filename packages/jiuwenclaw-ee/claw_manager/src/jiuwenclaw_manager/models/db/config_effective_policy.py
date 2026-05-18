"""配置生效策略表（service / agent 层级）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jiuwenclaw_manager.models.db.base import Base
from jiuwenclaw_manager.models.db.instance_scoped import INSTANCE_SCOPED_PK


class ConfigEffectiveServicePolicy(Base):
    __tablename__ = "config_effective_service_policy"
    __table_args__ = (INSTANCE_SCOPED_PK,)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    service_id: Mapped[str] = mapped_column(String(512), nullable=False)
    jiuwenclaw_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("instance_info.jiuwenclaw_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    match_expr: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    video_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    audio_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vision_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
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

    agent_policies: Mapped[list[ConfigEffectiveAgentPolicy]] = relationship(
        back_populates="service_policy",
        cascade="all, delete-orphan",
    )


class ConfigEffectiveAgentPolicy(Base):
    __tablename__ = "config_effective_agent_policy"
    __table_args__ = (
        INSTANCE_SCOPED_PK,
        ForeignKeyConstraint(
            ["jiuwenclaw_id", "service_policy_id"],
            [
                "config_effective_service_policy.jiuwenclaw_id",
                "config_effective_service_policy.id",
            ],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    agent_id: Mapped[str] = mapped_column(String(512), nullable=False)
    jiuwenclaw_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("instance_info.jiuwenclaw_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    service_policy_id: Mapped[int] = mapped_column(Integer, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    match_expr: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    video_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    audio_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vision_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
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

    service_policy: Mapped[ConfigEffectiveServicePolicy] = relationship(
        back_populates="agent_policies",
    )


class ConfigEffectiveGlobalPolicy(Base):
    __tablename__ = "config_effective_global_policy"
    __table_args__ = (
        INSTANCE_SCOPED_PK,
        UniqueConstraint("jiuwenclaw_id", name="uk_global_policy_jiuwenclaw_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    jiuwenclaw_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("instance_info.jiuwenclaw_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    default_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    video_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    audio_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vision_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel_ids: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
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
