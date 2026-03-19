# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Entity Type Definition

Entity and relation type definitions and multilingual description registries for extraction.
"""

from typing import Dict

from pydantic import BaseModel, Field

from .base import MultilingualBaseModel

# These will be populated in jiuwen/context/memory_engine/generation/graph/prompts/entity_extraction/*.py
ENTITY_DEFINITION_DESCRIPTION: Dict[str, str] = dict()
RELATION_DEFINITION_DESCRIPTION: Dict[str, str] = dict()
HUMAN_ENTITY_DESCRIPTION: Dict[str, str] = dict()
AI_ENTITY_DESCRIPTION: Dict[str, str] = dict()


class EntityDefAttr(MultilingualBaseModel):
    """Base Entity Type's Attributes"""

    content: str = Field(default="", description="{{[ent_summary]}}")


class EntityDef(BaseModel):
    """Base Entity Type"""

    name: str = "Entity"
    description: Dict[str, str] = ENTITY_DEFINITION_DESCRIPTION
    attributes: MultilingualBaseModel = Field(default_factory=EntityDefAttr)


class RelationDef(BaseModel):
    """Base Relation Type"""

    name: str = "Relation"
    description: Dict[str, str] = RELATION_DEFINITION_DESCRIPTION
    lhs: type[EntityDef]
    rhs: type[EntityDef]


class HumanEntity(EntityDef):
    """Human Entity Type"""

    name: str = "Human"
    description: Dict[str, str] = ENTITY_DEFINITION_DESCRIPTION


class AIEntity(EntityDef):
    """AI Assistant Entity Type"""

    name: str = "AI"
    description: Dict[str, str] = ENTITY_DEFINITION_DESCRIPTION
