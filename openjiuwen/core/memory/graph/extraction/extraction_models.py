# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Extraction Models

Pydantic models for entity/relation extraction output (declarations, summaries, etc.).
"""

from pydantic import Field

from .base import MultilingualBaseModel


# --- Datetime (Unused) ---
class Datetime(MultilingualBaseModel):
    """Representing Datetime (Unused)"""

    year: int = Field(description="{{[year]}}")
    month: int = Field(description="{{[month]}}")
    day: int = Field(description="{{[day]}}")
    hour: int = Field(description="{{[hour]}}")
    minute: int = Field(description="{{[minute]}}")
    second: int = Field(description="{{[second]}}")


# --- Schema Definition (Reference for Output Models)---
class EntityDeclaration(MultilingualBaseModel):
    """Entity Declaration"""

    name: str = Field(description="{{[ent_def_name]}}")
    entity_type_id: int = Field(description="{{[ent_def_type]}}")


class Duplication(MultilingualBaseModel):
    """Entity De-duplication"""

    name: str = Field(description="{{[ent_dupe_name]}}")
    id: int = Field(description="{{[ent_dupe_id]}}")
    duplicate_ids: list[int] = Field(description="{{[ent_dupe_id_list]}}")


class Fact(MultilingualBaseModel):
    """Factual Relation"""

    name: str = Field(description="{{[rel_name]}}")
    fact: str = Field(description="{{[rel_fact]}}")
    valid_since: str = Field(description="{{[rel_valid_since]}}")
    valid_until: str = Field(description="{{[rel_valid_until]}}")
    source_id: int = Field(description="{{[rel_source_id]}}")
    target_id: int = Field(description="{{[rel_target_id]}}")


class PossibleTimezone(MultilingualBaseModel):
    """Possible Timezone Guess"""

    name: str = Field(description="{{[tz_name]}}")
    offset_from_utc: str = Field(description="{{[tz_offset]}}")
    reasoning: str = Field(description="{{[tz_reason]}}")


# --- Output Models ---
class EntityExtraction(MultilingualBaseModel):
    """Output for entity declaration extraction"""

    extracted_entities: list[EntityDeclaration] = Field(description="{{[ent_ext_list]}}")


class EntitySummary(MultilingualBaseModel):
    """Output for entity summary & attribute extraction"""

    summary: str = Field(description="{{[ent_summary]}}")
    attributes: dict = Field(description="{{[ent_attributes]}}")


class EntityDuplication(MultilingualBaseModel):
    """Output for entity de-duplication"""

    duplicated_entities: list[Duplication] = Field(description="{{[ent_dupe_list]}}")


class RelationExtraction(MultilingualBaseModel):
    """Output for relation extraction"""

    extracted_relations: list[Fact] = Field(description="{{[rel_ext_list]}}")


class RelevantFacts(MultilingualBaseModel):
    """Output for fact/relation filtering"""

    brief_reasoning: str = Field(description="{{[rel_filter_reasoning]}}")
    relevant_relations: list[int] = Field(description="{{[rel_filter_list]}}")


class TimezonePredictions(MultilingualBaseModel):
    """Output for timezone prediction"""

    extracted_relations: list[PossibleTimezone] = Field(description="{{[tz_list]}}")


class MergeRelations(MultilingualBaseModel):
    """Output for relation merging"""

    need_merging: bool = Field(description="{{[rel_dupe_need_merge]}}")
    short_reasoning: str = Field(description="{{[rel_dupe_reasoning]}}")
    combined_content: str = Field(description="{{[rel_dupe_content]}}")
    duplicate_ids: list[int] = Field(description="{{[rel_dupe_id_list]}}")
    valid_since: str = Field(description="{{[rel_valid_since]}}")
    valid_until: str = Field(description="{{[rel_valid_until]}}")
