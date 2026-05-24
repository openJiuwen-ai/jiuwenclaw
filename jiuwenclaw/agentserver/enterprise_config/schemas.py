"""企业级配置路由上下文、加载结果与 ``template_ref`` 槽位定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TemplateRefSlot(StrEnum):
    """``template_ref`` JSON 键名，与 ``config_default_template_mapping.template_type`` 一致。"""

    DEFAULT_MODEL = "default_model"
    VIDEO_MODEL = "video_model"
    AUDIO_MODEL = "audio_model"
    VISION_MODEL = "vision_model"
    SKILL_WHITELIST = "skill_whitelist"
    EXTENSION_CONFIG = "extension_config"


SLOT_ENTITY_TABLE: dict[TemplateRefSlot, str] = {
    TemplateRefSlot.DEFAULT_MODEL: "model_template",
    TemplateRefSlot.VIDEO_MODEL: "model_template",
    TemplateRefSlot.AUDIO_MODEL: "model_template",
    TemplateRefSlot.VISION_MODEL: "model_template",
    TemplateRefSlot.SKILL_WHITELIST: "skill_whitelist_template",
    TemplateRefSlot.EXTENSION_CONFIG: "extension_config_template",
}

MODEL_SLOT_KEYS = frozenset({
    TemplateRefSlot.DEFAULT_MODEL,
    TemplateRefSlot.VIDEO_MODEL,
    TemplateRefSlot.AUDIO_MODEL,
    TemplateRefSlot.VISION_MODEL,
})

SLOT_TO_CONFIG_KEY: dict[TemplateRefSlot, str] = {
    TemplateRefSlot.DEFAULT_MODEL: "default",
    TemplateRefSlot.VISION_MODEL: "vision",
    TemplateRefSlot.AUDIO_MODEL: "audio",
    TemplateRefSlot.VIDEO_MODEL: "video",
}


def _normalize_slot_refs(raw: Any) -> list[str]:
    """将单槽位原始值规范为引用字符串列表（兼容历史单字符串写法）。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [text] if text else []
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                out.append(text)
        return out
    text = str(raw).strip()
    return [text] if text else []


def normalize_template_ref(value: Any) -> dict[str, list[str]]:
    """将 ``template_ref`` 规范为 ``{slot: [ref_string, ...]}``；空值键省略。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, raw in value.items():
        slot = str(key).strip()
        if not slot:
            continue
        refs = _normalize_slot_refs(raw)
        if refs:
            out[slot] = refs
    return out


@dataclass(frozen=True)
class RoutingContext:
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
    skill_whitelist: list[dict[str, Any]] | None = None
    extension_config: list[dict[str, Any]] | None = None
    service_policy_id: int | None = None
    agent_policy_id: int | None = None
    global_policy_id: int | None = None
    service_policy: dict[str, Any] | None = None
    agent_policy: dict[str, Any] | None = None
    global_policy: dict[str, Any] | None = None
    debug: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "routing": self.routing.as_dict(),
            "template_ref": dict(self.template_ref),
            "models": dict(self.models),
            "skill_whitelist": self.skill_whitelist,
            "extension_config": self.extension_config,
            "service_policy_id": self.service_policy_id,
            "agent_policy_id": self.agent_policy_id,
            "global_policy_id": self.global_policy_id,
            "debug": dict(self.debug),
        }
