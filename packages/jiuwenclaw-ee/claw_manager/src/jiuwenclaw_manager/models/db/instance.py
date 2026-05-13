"""核心持久化模型（与《JiuwenClaw Manager 模块设计文档》表名对齐；字段扩展走 data JSON）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jiuwenclaw_manager.models.db.base import Base


class InstanceInfo(Base):
    __tablename__ = "instance_info"

    jiuwenclaw_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    jiuwenclaw_name: Mapped[str] = mapped_column(String(128), nullable=False)
    creator_id: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    k8s_master_host: Mapped[str] = mapped_column(String(256), nullable=False)
    k8s_auth_type: Mapped[str] = mapped_column(String(32), nullable=False)
    k8s_auth_config: Mapped[str] = mapped_column(Text, nullable=False)
    k8s_namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    resource_quota: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False, server_default="default")
    space_id: Mapped[str] = mapped_column(String(64), nullable=False, server_default="default")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    services: Mapped[list[ServiceInstance]] = relationship(
        back_populates="instance",
        cascade="all, delete-orphan",
    )


class ServiceInstance(Base):
    __tablename__ = "service_instance"
    __table_args__ = (UniqueConstraint("jiuwenclaw_id", "service_id", name="uk_instance_service"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    jiuwenclaw_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("instance_info.jiuwenclaw_id", ondelete="CASCADE"), nullable=False
    )
    service_id: Mapped[str] = mapped_column(String(128), nullable=False)
    service_type: Mapped[str] = mapped_column(String(32), nullable=False)
    component_role: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(256), nullable=True)
    manager_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
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

    instance: Mapped[InstanceInfo] = relationship(back_populates="services")
