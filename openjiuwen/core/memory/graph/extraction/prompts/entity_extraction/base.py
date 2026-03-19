# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Entity Extraction Base

Shared formatting and schema helpers for entity extraction prompt generation.
"""

import json
from typing import Any, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.store.graph.utils import load_stored_time_from_db
from openjiuwen.core.memory.graph.extraction.base import MultilingualBaseModel
from openjiuwen.core.memory.graph.extraction.entity_type_definition import RelationDef

REGISTERED_LANGUAGE: set[str] = set()
SOURCE_DESCRIPTION: dict[str, str] = dict()
REF_JSON_OBJECT_DEF: dict[str, str] = dict()
OUTPUT_FORMAT: dict[str, str] = dict()
DISPLAY_ENTITY: dict[str, str] = dict()
MARK_CURRENT_MSG: dict[str, str] = dict()
MARK_HISTORY_MSG: dict[str, str] = dict()
RELATION_FORMAT: dict[str, str] = dict()
NO_RELATION_GIVEN: dict[str, str] = dict()
SCHEMA_INFO_HEADER = "\n\n---\n"


def format_schema_info(
    output_model: Optional[MultilingualBaseModel] = None, indent: int = 2, language: str = "cn"
) -> str:
    """Format LLM-readable schema into string to concatenate to prompt"""
    if output_model:
        out_str, ref_dict = output_model.readable_schema(language=language)
        schema_info = SCHEMA_INFO_HEADER
        if ref_dict:
            schema_info += f"# {REF_JSON_OBJECT_DEF[language]}\n"
            for k, v in ref_dict.items():
                v_str = json.dumps(v, ensure_ascii=False, indent=indent)
                schema_info += f"## {k}\n```json\n{v_str}\n```\n"
        schema_info += f"---\n# {OUTPUT_FORMAT[language]}\n```python\n{out_str}\n```"
        return schema_info
    return ""


def format_source_description(source_description: Optional[str] = None, language: str = "cn") -> str:
    """Format source description"""
    if source_description:
        return SOURCE_DESCRIPTION[language].format(source_description=source_description)
    return ""


def get_formatting_kwargs(
    source_description: Optional[str] = None,
    output_model: Optional[MultilingualBaseModel] = None,
    output_indent: int = 2,
    history: str = "",
    content: str = "",
    *,
    language: str = "cn",
    **kwargs,
) -> dict[str, str]:
    """Get various formatting keyword arguments sorted out"""
    # Assemble context
    context = ""
    if history:
        context += MARK_HISTORY_MSG[language].format(history=history)
    if content:
        context += MARK_CURRENT_MSG[language].format(content=content)

    return dict(
        source_description=format_source_description(source_description, language=language),
        extra_message=format_schema_info(output_model, indent=output_indent, language=language),
        context=context,
    )


def format_relation_definitions(relation_types: Optional[list[RelationDef]], language: str = "cn") -> str:
    """Format relation definitions"""
    if relation_types:
        template = RELATION_FORMAT[language]
        return "\n".join(
            template.format(
                name=rtype.name, description=rtype.description[language], lhs=rtype.lhs.name, rhs=rtype.rhs.name
            )
            for rtype in relation_types
        )
    return NO_RELATION_GIVEN[language]


def format_existing_relations(relations: list[dict], start_idx: int = 1, include_time: bool = True) -> str:
    """Format existing relations into a string list"""
    template = "{i}. {content}"
    string_builder = []
    for i, rel in enumerate(relations, start_idx):
        content = rel.get("content", "")
        valid_since = rel.get("valid_since", 0)
        valid_until = rel.get("valid_until", 0)
        if include_time and valid_since != -1:
            offset_since = rel.get("offset_since", 0)
            valid_since = load_stored_time_from_db(valid_since, offset_since).isoformat(timespec="seconds")
            content += f"\n{valid_since=}"
        if include_time and valid_until != -1:
            offset_until = rel.get("offset_until", 0)
            valid_until = load_stored_time_from_db(valid_until, offset_until).isoformat(timespec="seconds")
            content += f"\n{valid_until=}"
        string_builder.append(template.format(i=i, content=content))
    return "\n\n".join(string_builder)


def format_existing_entities(entities: list[dict], start_idx: int = 1, language: str = "cn") -> str:
    """Format existing entities into a string list"""
    template = DISPLAY_ENTITY[language]
    return "\n\n".join(template.format(i=i, **ent) for i, ent in enumerate(entities, start_idx))


def ensure_valid_language(language: str | Any, max_len: int) -> str:
    """Check to make sure language option is valid & supported"""
    if not isinstance(language, str):
        if hasattr(language, "__str__"):
            language = str(language)
        else:
            raise build_error(
                StatusCode.MEMORY_GRAPH_LANGUAGE_INVALID,
                error_msg="graph memory language option cannot be casted to string",
            )

    if language not in REGISTERED_LANGUAGE:
        raise build_error(
            StatusCode.MEMORY_GRAPH_LANGUAGE_INVALID,
            error_msg=f"graph memory does not support language {language}, registered: {REGISTERED_LANGUAGE}",
        )

    lan_len = len(language)
    if lan_len > max_len:
        raise build_error(
            StatusCode.MEMORY_GRAPH_LANGUAGE_INVALID,
            error_msg=f'language "{language}" exceeds max length set in db_storage_config.language ({max_len})',
        )

    return language
