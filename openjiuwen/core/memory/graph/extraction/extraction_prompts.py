# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Extraction Prompts

Prompt building and entity extraction orchestration by episode type.
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Type

from openjiuwen.core.foundation.prompt.template import PromptTemplate
from openjiuwen.core.foundation.store.graph.graph_object import Entity, Relation
from openjiuwen.core.memory.config.graph import EpisodeType
from openjiuwen.core.memory.graph.extraction.base import MULTILINGUAL_DESCRIPTION
from openjiuwen.core.memory.graph.extraction.prompts import TemplateManager
from openjiuwen.core.memory.graph.extraction.prompts.entity_extraction.base import (
    format_existing_entities,
    format_existing_relations,
    format_relation_definitions,
    get_formatting_kwargs,
)

from .entity_type_definition import EntityDef, RelationDef
from .extraction_models import (
    EntityDeclaration,
    EntityDuplication,
    EntityExtraction,
    EntitySummary,
    MergeRelations,
    RelationExtraction,
    RelevantFacts,
    TimezonePredictions,
)


def extract_entity_declaration(
    src_type: EpisodeType,
    content: str,
    history: str = "",
    description: Optional[str] = None,
    entity_types: Optional[List[EntityDef]] = None,
    *,
    language: str = "cn",
    extras: Optional[Dict] = None,
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]:
    """Assemble prompts for entity declaration (name) extraction"""
    operation = src_type.name.casefold()
    template_name = f"entity_extraction_{operation}_{language}"
    output_model = EntityExtraction
    kwargs = get_formatting_kwargs(
        source_description=description,
        output_model=output_model,
        output_indent=indent,
        content=content,
        history=history,
        language=language,
    )
    if extras:
        kwargs.update(extras)
    if entity_types is None:
        entity_types = [EntityDef()]
    kwargs["entity_types"] = "\n".join(
        f"{i}. {ent.name}{ent.description[language]}" for i, ent in enumerate(entity_types)
    )
    return kwargs, TemplateManager().get(template_name), output_model.response_format(language)


def extract_entity_attributes(
    entity: Entity,
    content: str,
    history: str = "",
    language: str = "cn",
    extras: Optional[Dict] = None,
    *,
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]:
    """Assemble prompts for entity summary & attribute extraction"""
    operation = "summary_create"
    template_name = f"entity_extraction_{operation}_{language}"
    output_model = EntitySummary
    kwargs = get_formatting_kwargs(
        output_model=output_model, indent=indent, content=content, history=history, language=language
    )
    kwargs["entity_name"] = entity.name
    kwargs["entity_summary"] = entity.content or ""
    if entity.attributes:
        kwargs["entity_attribute"] = json.dumps(entity.attributes, ensure_ascii=False, indent=indent)
    if extras:
        kwargs.update(extras)
    if entity.obj_type.casefold() == "human" and "summary_target" in kwargs:
        kwargs["summary_target"] = kwargs["summary_target"] * 2
    return kwargs, TemplateManager().get(template_name), output_model.response_format(language)


def extract_relation_declaration(
    relation_types: Optional[List[Type[RelationDef]]],
    entities: List[EntityDeclaration],
    reference_time: int,
    tz_info: Any,
    content: str,
    *,
    history: str = "",
    entity_types: Optional[List[EntityDef]] = None,
    description: Optional[str] = None,
    language: str = "cn",
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]:
    """Assemble prompts for relation extraction"""
    operation = "relation"
    template_name = f"entity_extraction_{operation}_{language}"
    output_model = RelationExtraction

    kwargs = get_formatting_kwargs(
        source_description=description,
        output_model=output_model,
        output_indent=indent,
        history=history,
        content=content,
        language=language,
    )
    if isinstance(tz_info, (dict, list)):
        tz_info = json.dumps(tz_info, ensure_ascii=False, indent=indent)
    else:
        tz_info = str(tz_info)
    kwargs["tz_info"] = tz_info
    kwargs["entities"] = format_new_entities(entities, entity_types, language=language)
    kwargs["relation_types"] = format_relation_definitions(relation_types, language=language)
    kwargs["reference_time"] = datetime.fromtimestamp(reference_time).isoformat(timespec="seconds")
    kwargs["id_range"] = f"1-{len(entities)}"
    return kwargs, TemplateManager().get(template_name), output_model.response_format(language)


def extract_timezone(
    content: str,
    history: str = "",
    description: Optional[str] = None,
    language: str = "cn",
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]:
    """Assemble prompts for possible timezone extraction"""
    operation = "timezone"
    template_name = f"entity_extraction_{operation}_{language}"
    output_model = TimezonePredictions

    kwargs = get_formatting_kwargs(
        source_description=description,
        output_model=output_model,
        output_indent=indent,
        history=history,
        content=content,
        language=language,
    )
    return kwargs, TemplateManager().get(template_name), output_model.response_format(language)


def merge_existing_entities(
    target: Entity,
    sources: List[Entity],
    language: str = "cn",
    extras: Optional[Dict] = None,
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]:
    """Assemble prompts for entity merging"""
    operation = "entity_merge"
    template_name = f"entity_extraction_{operation}_{language}"
    output_model = EntitySummary
    kwargs = get_formatting_kwargs(output_model=output_model, indent=indent, language=language)
    kwargs["entity_name"] = target.name
    kwargs["entity_summary"] = target.content or ""
    if target.attributes:
        kwargs["entity_attribute"] = json.dumps(target.attributes, ensure_ascii=False, indent=indent)
    kwargs["entities_to_merge"] = format_existing_entities([e.model_dump() for e in sources], language=language)
    if extras:
        kwargs.update(extras)
    return kwargs, TemplateManager().get(template_name), output_model.response_format(language)


def filter_relations_for_merge(
    target: Entity,
    relations: List[Relation],
    language: str = "cn",
    extras: Optional[Dict] = None,
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]:
    """Assemble prompts for relation filtering"""
    operation = "relation_filter"
    template_name = f"entity_extraction_{operation}_{language}"
    output_model = RelevantFacts
    relations = [r.model_dump() if isinstance(r, Relation) else r for r in relations]
    kwargs = get_formatting_kwargs(output_model=output_model, indent=indent, language=language)
    kwargs["entity_name"] = target.name
    kwargs["entity_summary"] = target.content or ""
    if target.attributes:
        kwargs["entity_attribute"] = json.dumps(target.attributes, ensure_ascii=False, indent=indent)
    kwargs["existing_relations"] = format_existing_relations(relations, include_time=False)
    if extras:
        kwargs.update(extras)
    return kwargs, TemplateManager().get(template_name), output_model.response_format(language)


def dedupe_entity_list(
    content: str,
    candidate_entities: List[EntityDeclaration],
    existing_entities: List[Dict],
    entity_types: Optional[List[EntityDef]] = None,
    history: str = "",
    *,
    description: Optional[str] = None,
    language: str = "cn",
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]:
    """Assemble prompts for entity de-duplication"""
    operation = "dedupe_entity"
    template_name = f"entity_extraction_{operation}_{language}"
    output_model = EntityDuplication

    kwargs = get_formatting_kwargs(
        source_description=description,
        output_model=output_model,
        output_indent=indent,
        history=history,
        content=content,
        language=language,
    )
    kwargs["entities"] = format_existing_entities(existing_entities, 1, language)
    kwargs["candidate_entities"] = format_new_entities(
        candidate_entities, entity_types, len(existing_entities) + 1, language=language
    )
    return kwargs, TemplateManager().get(template_name), output_model.response_format(language)


def dedupe_relation_list(
    content: str,
    relation: Relation,
    existing_relations: List[Dict | Relation],
    existing_entities: List[Entity],
    history: str = "",
    *,
    description: Optional[str] = None,
    language: str = "cn",
    indent: int = 2,
) -> Tuple[Dict[str, str], PromptTemplate, Dict[str, Any]]:
    """Assemble prompts for relation de-duplication"""
    operation = "dedupe_relation"
    template_name = f"entity_extraction_{operation}_{language}"
    output_model = MergeRelations

    kwargs = get_formatting_kwargs(
        source_description=description,
        output_model=output_model,
        output_indent=indent,
        history=history,
        content=content,
        language=language,
    )
    existing_relations = [r if isinstance(r, dict) else r.model_dump() for r in existing_relations]
    kwargs["entities"] = format_existing_entities(existing_entities, 1, language)
    kwargs["existing_relations"] = format_existing_relations(existing_relations, 1)
    kwargs["new_relation"] = format_existing_relations([relation.model_dump()], 0).removeprefix("0. ")
    return kwargs, TemplateManager().get(template_name), output_model.response_format(language)


def format_new_entities(
    entities: List[EntityDeclaration],
    entity_types: Optional[List[EntityDef]] = None,
    start_idx: int = 1,
    language: str = "cn",
) -> str:
    """Helper function for formatting new candidate entities into string list"""
    if entity_types:
        # Introduce entity types
        sep = MULTILINGUAL_DESCRIPTION[language][":"]
        entity_list = [
            f"{entity_types[type_id].name}{sep}{entity_types[type_id].description[language]}"
            for type_id in sorted({entity.entity_type_id for entity in entities})
        ] + ["---"]
        # List entities
        entity_list += [
            f"{i}. {entity.name} ({entity_types[entity.entity_type_id].name})"
            for i, entity in enumerate(entities, start_idx)
        ]
    else:
        # List entities
        entity_list = [f"{i}. {entity.name}" for i, entity in enumerate(entities, start_idx)]
    return "\n".join(entity_list)
