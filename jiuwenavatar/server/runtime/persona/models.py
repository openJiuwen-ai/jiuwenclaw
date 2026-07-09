# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persona data models — 数字分身身份模板与分身实例的数据模型."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Persona Template（身份模板）
# ---------------------------------------------------------------------------


class PersonaTriggerTemplate(BaseModel):
    """Persona 预置的触发器模板."""

    name: str = Field(..., description="触发器模板名称")
    type: str = Field("cron", description="触发器类型: cron / heartbeat / webhook / event")
    cron_expr: str | None = Field(None, description="Cron 表达式（type=cron 时必填）")
    interval_seconds: float | None = Field(None, description="心跳间隔秒数（type=heartbeat 时必填）")
    active_hours: dict | None = Field(None, description="生效时间段 {'start':'HH:MM','end':'HH:MM'}")
    webhook_path: str | None = Field(None, description="Webhook 路径（type=webhook 时必填）")
    event_source: str | None = Field(None, description="事件来源（type=event 时必填）")
    event_type: str | None = Field(None, description="事件类型（type=event 时必填）")
    prompt: str = Field(..., description="触发后发送给分身的 prompt")


class PersonaReportSection(BaseModel):
    """报告模板中的一个章节."""

    name: str = Field(..., description="章节名称")
    fields: list[str] = Field(default_factory=list, description="章节包含的字段列表")


class PersonaReportTemplate(BaseModel):
    """Persona 预置的报告模板."""

    title: str = Field("执行报告", description="报告标题模板")
    sections: list[PersonaReportSection] = Field(default_factory=list, description="报告章节定义")


class PersonaConfig(BaseModel):
    """Persona 身份模板配置.

    对应 resources/personas/*.yaml 文件的结构。
    """

    id: str = Field(..., description="Persona 唯一标识，如 'committer'")
    display_name: str = Field(..., description="显示名称，如 'Committer 分身'")
    description: str = Field("", description="分身描述")
    icon: str = Field("avatar", description="图标标识（对应前端 SVG，如 committer / developer）")
    version: str = Field("1.0.0", description="模板版本")

    # 编码能力（开发者 / Committer / 测试等涉及代码的分身）
    coding_capable: bool = Field(False, description="是否支持选择编码后端")
    coding_engines: list[str] = Field(
        default_factory=list,
        description="可选编码后端: jiuwen-coding / claude-code / codex",
    )
    default_coding_engine: str | None = Field(
        None,
        description="默认编码后端",
    )

    # 技能
    skills: list[str] = Field(default_factory=list, description="预置技能 ID 列表")

    # 触发器模板
    trigger_templates: list[PersonaTriggerTemplate] = Field(
        default_factory=list, description="预置触发器模板"
    )

    # 系统提示
    system_prompt: str = Field("", description="分身的系统提示词")

    # 报告模板
    report_template: PersonaReportTemplate = Field(
        default_factory=lambda: PersonaReportTemplate(),
        description="报告模板",
    )

    # 标签（用于分身广场筛选）
    tags: list[str] = Field(default_factory=list, description="标签列表，如 ['开发', '代码审阅']")

    # 是否为内置模板
    builtin: bool = Field(True, description="是否为内置 Persona 模板")


# ---------------------------------------------------------------------------
# Avatar Instance（分身实例）
# ---------------------------------------------------------------------------


class AvatarStatus(str, Enum):
    """分身实例状态."""

    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"


class AvatarConfig(BaseModel):
    """Avatar 数字分身实例配置.

    用户基于 Persona 创建的数字分身实例。
    """

    id: str = Field(default_factory=lambda: f"avatar-{uuid.uuid4().hex[:8]}")
    name: str = Field(..., description="分身名称，如 '我的 Committer 分身'")
    persona_id: str = Field(..., description="所基于的 Persona 模板 ID")
    persona_version: str = Field("1.0.0", description="创建时的 Persona 版本")

    # 运行状态
    status: AvatarStatus = Field(AvatarStatus.IDLE, description="当前状态")

    # 已安装的技能（从 Persona 继承 + 用户额外添加）
    skills: list[str] = Field(default_factory=list, description="已安装的技能 ID 列表")

    # 编码后端（仅 coding_capable 的 Persona 有效）
    coding_engine: str | None = Field(
        None,
        description="编码后端: jiuwen-coding / claude-code / codex",
    )

    # 系统提示（从 Persona 继承，用户可覆盖）
    system_prompt: str | None = Field(None, description="自定义系统提示，None 则使用 Persona 默认")

    # 关联的触发器 ID 列表
    trigger_ids: list[str] = Field(default_factory=list, description="关联的触发器 ID 列表")

    # 报告推送渠道
    report_channels: list[str] = Field(default_factory=list, description="报告推送渠道列表")

    # 时间戳
    created_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="创建时间",
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(),
        description="更新时间",
    )

    # 用户自定义配置
    extra: dict[str, Any] = Field(default_factory=dict, description="扩展配置字段")

    def get_effective_system_prompt(self, persona: PersonaConfig) -> str:
        """获取实际生效的系统提示（用户自定义 > Persona 默认）."""
        return self.system_prompt if self.system_prompt else persona.system_prompt

    def get_effective_skills(self, persona: PersonaConfig) -> list[str]:
        """获取实际生效的技能列表（Avatar 实例 > Persona 默认）."""
        if self.skills:
            return self.skills
        return persona.skills
