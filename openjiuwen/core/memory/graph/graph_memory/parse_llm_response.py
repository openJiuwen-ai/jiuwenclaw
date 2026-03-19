# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Parse LLM Response

Parsing LLM responses into graph entities, relations, and timestamps.
"""

import re
from typing import Optional, Union

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.store.graph import Entity, Relation
from openjiuwen.core.foundation.store.graph.utils import iso2timestamp
from openjiuwen.core.memory.graph.extraction.entity_type_definition import EntityDef
from openjiuwen.core.memory.graph.extraction.extraction_models import EntityDeclaration

MATCH_ISO_DATETIME = re.compile(
    r"([0-9]{1,4})-([0-9]{1,2})-([0-9]{1,2})T([0-9]{1,2}):([0-9]{1,2}):([0-9]{1,2})(?:Z|\+([0-9]{1,2}):([0-9]{1,2}))?"
)


def parse_iso(time_str: Optional[str]) -> tuple[int, int]:
    """Parse ISO 8601 datetime representation into UNIX timestamp and timezone offset"""
    if time_str is not None:
        time_match = MATCH_ISO_DATETIME.search(time_str)
        if time_match:
            yyyy, mm, dd = int(time_match.group(1)), int(time_match.group(2)), int(time_match.group(3))
            h, m, s = int(time_match.group(4)), int(time_match.group(5)), int(time_match.group(6))
            iso_str = f"{yyyy:04d}-{mm:02d}-{dd:02d}T{h:02d}:{m:02d}:{s:02d}"
            # manual align timezone offset as well
            offset_h, offset_m = time_match.group(7), time_match.group(8)
            if offset_h:
                offset_str = f"+{int(offset_h):02d}"
                if offset_m:
                    offset_str += f":{int(offset_m):02d}"
                iso_str += offset_str
            return iso2timestamp(iso_str)
    return -1, 0


def dict2relation(response: dict, entities: list[Entity], **kwargs) -> Optional[Relation]:
    """Parse response dictionary into proper relation objects"""
    if len(response) == 1:
        _response = next(iter(response.values()))
        if isinstance(_response, dict):
            response = _response
    source_id = response.get("source_id")
    target_id = response.get("target_id")
    try:
        source_id = int(source_id) - 1
        target_id = int(target_id) - 1
        if not (source_id >= 0 and target_id >= 0):
            raise build_error(
                StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                store_type="graph mem store",
                error_msg="relation source_id and target_id must be valid 1-based entity indices",
            )
        lhs = entities[source_id]
        rhs = entities[target_id]
        if lhs is rhs:
            rel_type = "EntityFact"
        else:
            rel_type = "Relation"
    except Exception:
        return None

    name = response.get("name", "RELATION")
    content = response.get("fact", name)
    valid_since, offset_since = parse_iso(response.get("valid_since", ""))
    valid_until, offset_until = parse_iso(response.get("valid_until", ""))

    return Relation(
        obj_type=rel_type,
        name=name,
        content=content,
        valid_since=valid_since,
        valid_until=valid_until,
        offset_since=offset_since,
        offset_until=offset_until,
        lhs=lhs,
        rhs=rhs,
        **kwargs,
    )


def parse_all_relations(
    relations: list[dict], entities: list[Union[EntityDeclaration, Entity]], entity_types: list[EntityDef], **kwargs
) -> tuple[list[Relation], list[Entity]]:
    """
    Parse all LLM extracted relation dictionaries & convert entity declaration in input into entities.
    Relations are also deduplicated by content, keeping the longest version and discarding shorter ones.
    """
    # Convert entity declaration into proper entities
    entities = declare_entities(entities, entity_types, **kwargs)

    # De-duplicate relations in case LLM starts repeating
    existing_contents = set()
    for relation in relations:
        new_content = relation.get("content", "").strip()
        if any(new_content in old_content for old_content in existing_contents):
            relation["content"] = ""
        else:
            relation["content"] = new_content
        existing_contents.add(new_content)

    # Parse relation extraction results
    relations = [rel for rel in (dict2relation(rel, entities, **kwargs) for rel in relations) if rel]

    # De-duplicate entities before returning (should not de-dupe before relations are parsed)
    return relations, list({entity.uuid: entity for entity in entities}.values())


def declare_entities(
    entities: list[Union[EntityDeclaration, Entity]], entity_types: list[EntityDef], **kwargs
) -> list[Entity]:
    """Convert all entity declarations in input list into proper entities"""
    type_id_max = len(entity_types) - 1
    return [
        (
            Entity(
                name=ent.name, content="", obj_type=entity_types[min(ent.entity_type_id, type_id_max)].name, **kwargs
            )
            if isinstance(ent, EntityDeclaration)
            else ent
        )
        for ent in entities
    ]


def resolve_entities(
    candidates: list[EntityDeclaration], existing: list[Entity], duplication: list[dict]
) -> tuple[list[Union[EntityDeclaration, Entity]], list[tuple[Entity, list[Entity]]], set[str]]:
    """Resolve / de-duplicate entities without changing the length of entity list.
    Returns (resolved_entities, merging_args, entity_uuids_to_remove).

    If existing entities need merging:
    - merging_args contains argument tuples for merging tasks: [(target, [source_1, source_2, ...]), ...]
    - entity_uuids_to_remove contains uuid of entities to remove due to merging
    """
    result = candidates.copy()
    name_lookup = {ent.name: ent for ent in existing}
    uuid_lookup = {ent.uuid: ent for ent in existing}
    num_existing = len(existing)
    num_entities = len(candidates) + num_existing
    merge_map: dict[str, set[str]] = dict()
    is_target: dict[str, str] = dict()
    for dup in duplication:
        dup_id = dup.get("id", "")
        tgt_entity = None
        if isinstance(dup_id, int) or (isinstance(dup_id, str) and dup_id.isnumeric()):
            dup_id = int(dup_id) - 1
            if dup_id < num_existing:
                tgt_entity = existing[dup_id]
        else:
            tgt_entity = name_lookup.get(dup.get("name"))
        if tgt_entity is not None:
            _parse_entity_merging(
                dup,
                merge_map,
                is_target,
                result,
                existing,
                tgt_entity=tgt_entity,
                num_entities=num_entities,
                num_existing=num_existing,
            )

    # Resolve merge_dict, ensure sync between merge_dict and result
    merge_dict = {tgt_uuid: [uuid_lookup[src] for src in src_uuids] for tgt_uuid, src_uuids in merge_map.items()}
    merge_dict = _resolve_merge_dict(merge_dict, result, uuid_lookup)

    return (
        result,
        [(uuid_lookup[tgt_uuid], src_entities) for tgt_uuid, src_entities in merge_dict.items()],
        _find_to_remove(merge_dict),
    )


def parse_relation_merging(response: dict, relation: Relation, existing_relations: list[dict]) -> set[str]:
    """Parse LLM response for relation merging, return list of uuids to remove from database"""
    to_remove = set()
    num_existing = len(existing_relations)
    need_merge = response.get("need_merging")
    content = str(response.get("combined_content", "")).strip()
    dup_ids = response.get("duplicate_ids", [])

    if need_merge and content:
        relation.content = content
        valid_since, offset_since = parse_iso(response.get("valid_since", ""))
        if valid_since >= 0:
            relation.valid_since, relation.offset_since = valid_since, offset_since
        valid_until, offset_until = parse_iso(response.get("valid_until", ""))
        if valid_until >= 0:
            relation.valid_until, relation.offset_until = valid_until, offset_until
        if isinstance(dup_ids, list):
            for i in dup_ids:
                if 0 < i <= num_existing:
                    to_remove.add(existing_relations[i - 1].get("uuid", "ERROR"))
    return to_remove


def _resolve_merge_dict(
    merge_dict: dict[str, list[Entity]], result: list[Union[EntityDeclaration, Entity]], uuid_lookup: dict[str, Entity]
) -> dict[str, list[Entity]]:
    """Resolve merge_dict, ensure sync between merge_dict and result"""
    merge_dict_sorted = dict()
    for tgt_uuid, src_entities in merge_dict.items():
        tgt = uuid_lookup[tgt_uuid]
        # Record how many entries we should replace in result list
        replace_idx_result_list = []
        replace_count = dict.fromkeys([src.uuid for src in src_entities], 0)
        for src in src_entities:
            if src in result:
                for idx, e in enumerate(result):
                    if src == e:
                        replace_idx_result_list.append(idx)
                        replace_count[src.uuid] += 1
        # Replace all appearances
        if tgt in result or not replace_idx_result_list:
            # If target is in result list or no one is in result list
            merge_dict_sorted[tgt_uuid] = src_entities
            for idx in replace_idx_result_list:
                result[idx] = tgt
        else:
            # If at least one source entity is in result list while target entity is not
            new_tgt_uuid = sorted(replace_count.items(), key=lambda x: x[1], reverse=True)[0][0]
            new_tgt = uuid_lookup[new_tgt_uuid]
            src_entities.append(tgt)
            src_entities.remove(new_tgt)
            merge_dict_sorted[new_tgt_uuid] = src_entities
            for idx in replace_idx_result_list:
                result[idx] = new_tgt
    return merge_dict_sorted


def _parse_entity_merging(
    dup: dict,
    merge_map: dict[str, set[str]],
    is_target: dict[str, str],
    result: list[Union[EntityDeclaration, Entity]],
    existing: list[Entity],
    *,
    tgt_entity: Entity,
    num_entities: int,
    num_existing: int,
):
    """Parse one entity merging request"""
    for dup_id in dup.get("duplicate_ids", []):
        if isinstance(dup_id, int) or (isinstance(dup_id, str) and dup_id.isnumeric()):
            dup_id = int(dup_id) - 1
            if num_existing <= dup_id < num_entities:
                # Existing entity should replace new candidate
                result[dup_id - num_existing] = tgt_entity
            elif 0 <= dup_id < num_existing:
                # Existing entity should replace another existing entity
                src_entity = existing[dup_id]
                if tgt_entity.uuid == src_entity.uuid:
                    continue
                if tgt_entity.uuid in merge_map:
                    is_target[src_entity.uuid] = tgt_entity.uuid
                    merge_map[tgt_entity.uuid].add(src_entity.uuid)
                elif src_entity.uuid in merge_map:
                    is_target[tgt_entity.uuid] = src_entity.uuid
                    merge_map[src_entity.uuid].add(tgt_entity.uuid)
                else:
                    tgt_of_tgt_uuid = is_target.get(tgt_entity.uuid, tgt_entity.uuid)
                    is_target[src_entity.uuid] = tgt_of_tgt_uuid
                    if tgt_of_tgt_uuid not in merge_map:
                        merge_map[tgt_of_tgt_uuid] = {src_entity.uuid}
                    else:
                        merge_map[tgt_of_tgt_uuid].add(src_entity.uuid)


def _find_to_remove(merge_dict: dict[str, list[Entity]]) -> set[str]:
    """Find the set of uuids for entities to be deleted from db"""
    to_remove = set()
    for entity_list in merge_dict.values():
        to_remove.update(e.uuid for e in entity_list)
    to_remove.difference_update(merge_dict.keys())
    return to_remove
