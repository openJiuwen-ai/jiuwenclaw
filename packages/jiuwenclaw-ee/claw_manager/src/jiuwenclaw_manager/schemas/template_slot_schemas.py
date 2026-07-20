"""``template_ref`` 槽位常量（无 template_ref / policy schema 依赖）。"""

from __future__ import annotations

from typing import Literal

_MODEL_TEMPLATE_SLOTS: tuple[str, ...] = (
    "default_model",
    "video_model",
    "audio_model",
    "vision_model",
)

SKILL_WHITELIST_SLOT = "skill_whitelist"
EXTENSION_CONFIG_SLOT = "extension_config"
SERVICE_CONFIG_SLOT = "service_config"
EMBEDDING_MODEL_SLOT = "embedding_model"

_TEMPLATE_REF_SLOTS: tuple[str, ...] = (
    *_MODEL_TEMPLATE_SLOTS,
    SKILL_WHITELIST_SLOT,
    EXTENSION_CONFIG_SLOT,
    SERVICE_CONFIG_SLOT,
    EMBEDDING_MODEL_SLOT,
)

MODEL_TEMPLATE_SLOTS: frozenset[str] = frozenset(_MODEL_TEMPLATE_SLOTS)
TEMPLATE_REF_SLOTS: frozenset[str] = frozenset(_TEMPLATE_REF_SLOTS)
SINGLE_VALUE_TEMPLATE_REF_SLOTS: frozenset[str] = frozenset(
    (*_MODEL_TEMPLATE_SLOTS, SERVICE_CONFIG_SLOT, EMBEDDING_MODEL_SLOT)
)

DefaultTemplateMappingTypeLiteral = Literal[
    "default_model",
    "video_model",
    "audio_model",
    "vision_model",
    "skill_whitelist",
    "extension_config",
    "service_config",
    "embedding_model",
]

MappingScopeTypeLiteral = Literal["user", "group", "bot"]
MAPPING_SCOPE_TYPES: frozenset[str] = frozenset({"user", "group", "bot"})

__all__ = (
    "MODEL_TEMPLATE_SLOTS",
    "SKILL_WHITELIST_SLOT",
    "EXTENSION_CONFIG_SLOT",
    "SERVICE_CONFIG_SLOT",
    "EMBEDDING_MODEL_SLOT",
    "SINGLE_VALUE_TEMPLATE_REF_SLOTS",
    "TEMPLATE_REF_SLOTS",
    "DefaultTemplateMappingTypeLiteral",
    "MappingScopeTypeLiteral",
    "MAPPING_SCOPE_TYPES",
)
