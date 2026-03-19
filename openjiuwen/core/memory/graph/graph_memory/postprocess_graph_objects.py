# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Postprocess Graph Objects

Validation and processing of graph entities, episodes, and relations.
"""

__all__ = [
    "validate_entities_episodes",
    "create_episode",
    "process_relations",
    "process_entities",
    "parse_relation_uuids_to_remove",
]

import asyncio
from typing import List, Tuple

from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.foundation.store.graph import (
    ENTITY_COLLECTION,
    EPISODE_COLLECTION,
    RELATION_COLLECTION,
    Entity,
    Episode,
    GraphStore,
    Relation,
)
from openjiuwen.core.foundation.store.graph.utils import ensure_unique_uuids
from openjiuwen.core.memory.graph.extraction.parse_response import parse_json

from .parse_llm_response import parse_relation_merging
from .states import GraphMemState
from .utils import update_entity


def validate_entities_episodes(entities: List[Entity], current_episode: Episode, state: GraphMemState):
    """Ensure entity-episode connections are in sync"""

    current_episode.entities = list(
        set([e if isinstance(e, str) else e.uuid for e in current_episode.entities] + [e.uuid for e in entities])
    )
    state.mem_update_skip_embed.updated_entity = [
        e for e in state.mem_update_skip_embed.updated_entity if e not in state.mem_update.updated_entity
    ]

    for tgt_uuid, merge_info in state.merge_infos.items():
        for src_uuid, src in merge_info.source.items():
            for ep_uuid in src.episodes:
                ep = state.lookup_table.episodes.get(ep_uuid)
                if ep is None:
                    continue
                updated = False
                if src_uuid in ep.entities:
                    ep.entities.remove(src_uuid)
                    updated = True
                elif src in ep.entities:
                    ep.entities.remove(src)
                    updated = True
                if updated:
                    ep.entities.append(tgt_uuid)
                    if ep not in state.mem_update_skip_embed.updated_episode:
                        state.mem_update_skip_embed.updated_episode.append(ep)

    for episode in state.mem_update_skip_embed.updated_episode + [current_episode]:
        for entity in state.mem_update.updated_entity + state.mem_update_skip_embed.updated_entity:
            ep2e = entity.uuid in episode.entities
            e2ep = episode.uuid in entity.episodes
            # Entity's episode connection info is always up-to-date
            if ep2e and not e2ep:  # [Episode] -> [Entity], remove Entity from Episode
                episode.entities.remove(entity.uuid)
            elif e2ep and not ep2e:  # [Episode] <- [Entity], add Entity to Episode
                episode.entities.append(entity.uuid)
        episode.entities = list(set(episode.entities))


async def create_episode(database: GraphStore, user_id: str, content: str, state: GraphMemState) -> Episode:
    """Create new episode"""

    current_episode = Episode(
        created_at=state.current_timestamp,
        valid_since=state.reference_timestamp,
        user_id=user_id,
        obj_type=state.episode_type.name,
        language=state.prompting.language,
        content=content,
    )

    # Resolve episodes
    current_episode.uuid = (
        await ensure_unique_uuids(
            database, ids=[current_episode.uuid], collection=EPISODE_COLLECTION, skip=state.strategy.skip_uuid_dedupe
        )
    )[0]
    state.mem_update.added_episode.append(current_episode)
    return current_episode


async def process_relations(
    database: GraphStore, entities: List[Entity], relations: List[Relation], state: GraphMemState
):
    """Process relations"""

    to_resolve = state.tmp_buffer
    to_resolve.clear()
    # Remove Deprecated Relations & Entities
    for relation_uuid in state.mem_update.removed_relation:
        for entity in entities + state.mem_update_skip_embed.updated_entity:
            if relation_uuid in entity.relations:
                entity.relations.remove(relation_uuid)
            relation = state.lookup_table.relations.get(relation_uuid)
            if relation in entity.relations:
                entity.relations.remove(relation)

    # Process relations
    for relation in relations:
        relation.update_connected_entities()
        state.mem_update.added_relation.append(relation)
        to_resolve.append(relation.uuid)

    # Resolve relation uuids
    if to_resolve:
        unique_uuids = await ensure_unique_uuids(
            database, ids=to_resolve, collection=RELATION_COLLECTION, skip=state.strategy.skip_uuid_dedupe
        )
        for relation, unique_uuid in zip(state.mem_update.added_relation, unique_uuids):
            relation.uuid = unique_uuid


async def process_entities(
    database: GraphStore, entities: List[Entity], current_episode: Episode, state: GraphMemState
):
    """Process entities"""

    to_resolve = state.tmp_buffer
    to_resolve.clear()
    # Complete the remaining merging tasks
    for future in state.merging_tasks:
        response = await future
        entity = state.merging_tasks_entities[future]
        update_entity(entity, response.content, state.prompting.schema_entity_extraction)
        if entity not in entities:
            entities.append(entity)
    # Process entities
    for entity in entities:
        entity.content = entity.content.removeprefix("\n")
        state.to_remove.clear()
        for r in entity.relations:
            r_uuid = r if isinstance(r, str) else r.uuid
            if r_uuid in state.mem_update.removed_relation:
                state.to_remove.append(r)
        for r in state.to_remove:
            entity.relations.remove(r)
        if current_episode.uuid not in entity.episodes and current_episode not in entity.episodes:
            entity.episodes.append(current_episode.uuid)
        entity.language = state.prompting.language
        if entity.uuid in state.retrieved_entities:
            state.mem_update.updated_entity.append(entity)
        else:
            state.mem_update.added_entity.append(entity)
            to_resolve.append(entity.uuid)
    await _resolve_entity_uuid(database, state)


async def parse_relation_uuids_to_remove(
    dedupe_relation_tasks: List[Tuple[Relation, List[Relation], asyncio.Future]], state: GraphMemState
):
    """Parse relation uuids to remove"""
    for relation, current_relations, future in dedupe_relation_tasks:
        try:
            result = await future
            dedupe_relation = (
                parse_json(getattr(result, "content"), output_schema=state.prompting.schema_relation_merge) or {}
            )
            state.to_remove.extend(parse_relation_merging(dedupe_relation, relation, current_relations))
        except Exception as e:
            memory_logger.info("Graph Memory: Failed to parse relation uuids to remove: %s", e)


async def _resolve_entity_uuid(database: GraphStore, state: GraphMemState):
    """Resolve entity uuids"""

    to_resolve = state.tmp_buffer
    if to_resolve:
        unique_uuids = await ensure_unique_uuids(
            database, ids=to_resolve, collection=ENTITY_COLLECTION, skip=state.strategy.skip_uuid_dedupe
        )
        for entity, unique_uuid in zip(state.mem_update.added_entity, unique_uuids):
            entity.uuid = unique_uuid
