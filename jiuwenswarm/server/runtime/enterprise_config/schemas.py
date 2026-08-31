"""企业级配置路由上下文、加载结果与 ``template_ref`` 槽位定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

SERVICE_CONFIG_SLOT = "service_config"
SERVICE_CONFIG_TABLE = "service_config_template"


class TemplateRefSlot(StrEnum):
    """``template_ref`` JSON 键名（与 agent_template.template_ref 槽位一致）。"""

    DEFAULT_MODEL = "default_model"
    VIDEO_MODEL = "video_model"
    AUDIO_MODEL = "audio_model"
    VISION_MODEL = "vision_model"
    EMBEDDING_MODEL = "embedding_model"
    SKILL_WHITELIST = "skill_whitelist"
    EXTENSION_CONFIG = "extension_config"
    SERVICE_CONFIG = "service_config"


SLOT_ENTITY_TABLE: dict[TemplateRefSlot, str] = {
    TemplateRefSlot.DEFAULT_MODEL: "model_template",
    TemplateRefSlot.VIDEO_MODEL: "model_template",
    TemplateRefSlot.AUDIO_MODEL: "model_template",
    TemplateRefSlot.VISION_MODEL: "model_template",
    TemplateRefSlot.EMBEDDING_MODEL: "embedding_template",
    TemplateRefSlot.SKILL_WHITELIST: "skill_whitelist_template",
    TemplateRefSlot.EXTENSION_CONFIG: "extension_config_template",
    TemplateRefSlot.SERVICE_CONFIG: "service_config_template",
}

MODEL_SLOT_KEYS = frozenset({
    TemplateRefSlot.DEFAULT_MODEL,
    TemplateRefSlot.VIDEO_MODEL,
    TemplateRefSlot.AUDIO_MODEL,
    TemplateRefSlot.VISION_MODEL,
})

DEFAULT_AGENT_LOAD_SLOTS = frozenset({
    *MODEL_SLOT_KEYS,
    TemplateRefSlot.EMBEDDING_MODEL,
    TemplateRefSlot.SKILL_WHITELIST,
    TemplateRefSlot.EXTENSION_CONFIG,
})


def normalize_template_ref(value: Any) -> dict[str, list[str]]:
    """将 ``template_ref`` 规范为 ``{slot: [ref_string, ...]}``；空值键省略。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("template_ref must be a JSON object")
    out: dict[str, list[str]] = {}
    for key, raw in value.items():
        slot = str(key).strip()
        if not slot or raw is None:
            continue
        if not isinstance(raw, list):
            raise ValueError(f"template_ref[{slot!r}] must be a list")
        refs = [
            str(item).strip()
            for item in raw
            if item is not None and str(item).strip()
        ]
        if refs:
            out[slot] = refs
    return out


@dataclass(frozen=True)
class RoutingContext:
    """企业配置路由三元组；不含 ``gateway_id``（Agent 业务不消费）。"""

    group_id: str
    bot_id: str
    user_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "group_id": self.group_id,
            "bot_id": self.bot_id,
            "user_id": self.user_id,
        }


@dataclass
class EffectiveEnterpriseConfig:
    """单次路由上下文下解析完成的企业级配置快照。"""

    routing: RoutingContext
    template_ref: dict[str, list[str]] = field(default_factory=dict)
    models: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    embedding: list[dict[str, Any]] | None = None
    skill_whitelist: list[dict[str, Any]] | None = None
    extension_config: list[dict[str, Any]] | None = None
    service_config: list[dict[str, Any]] | None = None
    service_id: str | None = None
    agent_id: str | None = None
    workspace_dir: str | None = None
    send_file_allowed: bool = True
    resource_id: str | None = None
    ref_template_id: str | None = None
    agent_template: dict[str, Any] | None = None
    instance_agent_resource: dict[str, Any] | None = None
    debug: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "routing": self.routing.as_dict(),
            "template_ref": dict(self.template_ref),
            "models": dict(self.models),
            "embedding": self.embedding,
            "skill_whitelist": self.skill_whitelist,
            "extension_config": self.extension_config,
            "service_config": self.service_config,
            "service_id": self.service_id,
            "agent_id": self.agent_id,
            "workspace_dir": self.workspace_dir,
            "send_file_allowed": self.send_file_allowed,
            "resource_id": self.resource_id,
            "ref_template_id": self.ref_template_id,
            "agent_template": self.agent_template,
            "instance_agent_resource": self.instance_agent_resource,
            "debug": dict(self.debug),
        }


__all__ = (
    "SERVICE_CONFIG_SLOT",
    "SERVICE_CONFIG_TABLE",
    "DEFAULT_AGENT_LOAD_SLOTS",
    "EffectiveEnterpriseConfig",
    "MODEL_SLOT_KEYS",
    "RoutingContext",
    "SLOT_ENTITY_TABLE",
    "TemplateRefSlot",
    "normalize_template_ref",
)
