# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Entity Extraction (English)

English language strings and prompt descriptions for entity extraction.
"""

from openjiuwen.core.memory.graph.extraction.entity_type_definition import (
    AI_ENTITY_DESCRIPTION,
    ENTITY_DEFINITION_DESCRIPTION,
    HUMAN_ENTITY_DESCRIPTION,
    RELATION_DEFINITION_DESCRIPTION,
)

from ...base import MULTILINGUAL_DESCRIPTION
from .base import (
    DISPLAY_ENTITY,
    MARK_CURRENT_MSG,
    MARK_HISTORY_MSG,
    NO_RELATION_GIVEN,
    OUTPUT_FORMAT,
    REF_JSON_OBJECT_DEF,
    REGISTERED_LANGUAGE,
    RELATION_FORMAT,
    SOURCE_DESCRIPTION,
)

LANGUAGE_CODE = "en"


def register_language():
    """Register language"""

    SOURCE_DESCRIPTION[LANGUAGE_CODE] = "\n<source_description>\n{source_description}\n</source_description>\n"
    REF_JSON_OBJECT_DEF[LANGUAGE_CODE] = "Definition for relevant JSON objects"
    OUTPUT_FORMAT[LANGUAGE_CODE] = "Output Definition (Final Output NEEDS to be JSON)"
    DISPLAY_ENTITY[LANGUAGE_CODE] = "{i}. {name}:\n{content}"
    MARK_CURRENT_MSG[LANGUAGE_CODE] = "<current_messages>\n{content}\n</current_messages>\n"
    MARK_HISTORY_MSG[LANGUAGE_CODE] = "<history_messages>\n{history}\n</history_messages>\n"
    RELATION_FORMAT[LANGUAGE_CODE] = "{name} (<{lhs}>-[{name}]-<{rhs}>): {description}"
    NO_RELATION_GIVEN[LANGUAGE_CODE] = "None"
    ENTITY_DEFINITION_DESCRIPTION[LANGUAGE_CODE] = ": Default entity type, pick this if no other option is suitable."
    HUMAN_ENTITY_DESCRIPTION[LANGUAGE_CODE] = ": Represent a human, can either be the user or other people."
    AI_ENTITY_DESCRIPTION[LANGUAGE_CODE] = ": Represent an AI assistant, can be a chatbot or other types of AI agents."
    RELATION_DEFINITION_DESCRIPTION[LANGUAGE_CODE] = ": Default relation type."
    MULTILINGUAL_DESCRIPTION[LANGUAGE_CODE] = {
        # Entity
        "{{[ent_def_name]}}": "Name of extracted entity",
        "{{[ent_def_type]}}": "Type ID of extracted entity, needs to be from the list of provided entity types",
        "{{[ent_ext_list]}}": "List of extracted entities",
        "{{[ent_summary]}}": "Important information regarding the entity, a short & concise summary within 250 words",
        "{{[ent_attributes]}}": "Entity attributes",
        "{{[ent_info]}}": "Extracted entity attributes and summary",
        "{{[ent_valid_since]}}": "Date for when this entity starts to be valid, please use ISO format "
        "YYYY-MM-DDTHH:MM:SS[+HH:MM]",
        "{{[ent_valid_until]}}": "Date for when this entity stops being valid, please use ISO format "
        "YYYY-MM-DDTHH:MM:SS[+HH:MM]",
        "{{[ent_dupe_name]}}": "Name of existing entity",
        "{{[ent_dupe_id]}}": "ID of existing entity",
        "{{[ent_dupe_id_list]}}": "List of IDs for entities that may be deplicate of this existing entity",
        "{{[ent_dupe_list]}}": "List of duplicate entities",
        # Relation
        "{{[rel_valid_since]}}": "Date for when this fact / relation starts to be valid, please use ISO format "
        "YYYY-MM-DDTHH:MM:SS[+HH:MM]",
        "{{[rel_valid_until]}}": "Date for when this fact / relation stops being valid, please use ISO format "
        "YYYY-MM-DDTHH:MM:SS[+HH:MM]",
        "{{[rel_fact]}}": "Fact regarding the relation",
        "{{[rel_name]}}": "Name of factual relation",
        "{{[rel_source_name]}}": "Name of source entity",
        "{{[rel_source_id]}}": "ID of source entity",
        "{{[rel_target_name]}}": "Name of target entity",
        "{{[rel_target_id]}}": "ID of target entity",
        "{{[rel_ext_list]}}": "List of extracted relations",
        "{{[rel_filter_list]}}": "List of IDs for facts that are likely relevant to the provided entity",
        "{{[rel_filter_reasoning]}}": "A brief reasoning for why certain facts are irrelevant, no need to be "
        "extensive (within 150 words)",
        "{{[rel_dupe_need_merge]}}": "Whether merging is required, if no merge then other fields can be left empty",
        "{{[rel_dupe_reasoning]}}": "Why do we need to merge the new relation with existing?",
        "{{[rel_dupe_content]}}": "Updated fact regarding the relation",
        "{{[rel_dupe_id_list]}}": "List of IDs for existing relations that should be merged within the new relation",
        # Datetime
        "{{[year]}}": "year",
        "{{[month]}}": "month",
        "{{[day]}}": "day",
        "{{[hour]}}": "hour",
        "{{[minute]}}": "minute",
        "{{[second]}}": "second",
        "{{[tz_name]}}": "Timezone's name",
        "{{[tz_offset]}}": "Offset from UTC (use +HH:MM format)",
        "{{[tz_reason]}}": "Why this candidate",
        "{{[tz_list]}}": "List of candidate timezones",
        # Misc.
        ":": ":",
    }
    REGISTERED_LANGUAGE.add(LANGUAGE_CODE)
