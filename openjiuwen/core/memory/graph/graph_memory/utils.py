# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph Memory Utils

Message conversion and entity update helpers for graph memory.
"""

from typing import Any, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.foundation.prompt.template import PromptTemplate
from openjiuwen.core.foundation.store.graph import Entity
from openjiuwen.core.memory.graph.extraction.parse_response import parse_json


def msg2dict(messages: list[dict | BaseMessage], preserve_meta: bool = False, **kwargs) -> list[dict[str, Any]]:
    """Convert a list of BaseMessage into a list of dict.

    Args:
        messages (list[dict | BaseMessage]): list of BaseMessage to convert.
        preserve_meta (bool, optional): preserve the extra fields in BaseMessage. Defaults to False.

    Returns:
        list[dict[str, Any]]: list of converted dict messages.
    """
    if not (isinstance(messages, list) and all(isinstance(msg, (dict, BaseMessage)) for msg in messages)):
        raise build_error(
            StatusCode.MEMORY_STORE_VALIDATION_INVALID,
            store_type="graph memory",
            error_msg="Input is not a list of dict or BaseMessage",
        )
    if preserve_meta:
        return [msg.model_dump(mode="python", **kwargs) if isinstance(msg, BaseMessage) else msg for msg in messages]
    return [dict(role=msg.role, content=msg.content) if isinstance(msg, BaseMessage) else msg for msg in messages]


def update_entity(entity: Entity, response: str, extraction_schema: dict):
    """Update entity content based on LLM response"""
    extracted_entity_info = parse_json(response, output_schema=extraction_schema) or {}
    if isinstance(extracted_entity_info, list):
        extracted_entity_info = extracted_entity_info[0]
    if isinstance(extracted_entity_info, str):
        extracted_entity_info = dict(summary=extracted_entity_info)
    _parse_summary(entity, extracted_entity_info)
    _parse_attributes(entity, extracted_entity_info)


def _parse_summary(entity: Entity, extracted_entity_info: dict[str, Any]):
    """Process extracted summary"""
    summary = extracted_entity_info.get("summary", "")
    if isinstance(summary, (list, set)):
        summary = "\n".join(line.strip() for line in summary)
    elif not isinstance(summary, str):
        summary = str(summary) if summary else ""
    summary = summary.strip()
    summary_cleaned = summary.casefold()
    if summary and all(null_word not in summary_cleaned for null_word in ["null", "none", "empty"]):
        entity.content = summary


def _parse_attributes(entity: Entity, extracted_entity_info: dict[str, Any]):
    """Process extracted attributes"""
    attributes = extracted_entity_info.get("attributes")
    if isinstance(attributes, str):
        attributes = parse_json(attributes)
    elif isinstance(attributes, (list, set)):
        try:
            attributes = dict(attributes)
        except Exception as e:
            memory_logger.info("Graph Memory: Failed to parse extracted entity attribute: %s", e)
    if not isinstance(attributes, dict):
        attributes = {}  # Cannot parse, don't risk saving bad data to database
    if attributes:
        entity.attributes = attributes


def assemble_invoke_params(kwargs: dict, template: PromptTemplate, output_model: Optional[dict] = None):
    """Assemble LLM client invoke parameters"""
    params = dict(messages=msg2dict(template.format(kwargs).content))
    if output_model:
        params["response_format"] = output_model
    return params
