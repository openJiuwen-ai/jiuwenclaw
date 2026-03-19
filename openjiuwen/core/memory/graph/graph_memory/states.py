# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph Memory States

State and lookup structures for graph memory updates.
"""

import asyncio
import contextlib
import signal
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Callable, Mapping

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.foundation.store.base_embedding import Embedding
from openjiuwen.core.foundation.store.graph import ENTITY_COLLECTION, RELATION_COLLECTION, GraphStore
from openjiuwen.core.foundation.store.graph.config import GraphConfig
from openjiuwen.core.foundation.store.graph.graph_object import BaseGraphObject, Entity, Episode, Relation
from openjiuwen.core.foundation.store.graph.utils import get_current_utc_timestamp
from openjiuwen.core.memory.config.graph import AddMemStrategy, EpisodeType
from openjiuwen.core.memory.graph.extraction.entity_type_definition import EntityDef
from openjiuwen.core.memory.graph.extraction.extraction_models import (
    EntityDuplication,
    EntitySummary,
    MergeRelations,
    RelevantFacts,
)


def nested_clear_dataclass(data_obj: Any):
    """Recursively call clear method on input dataclass's fields"""

    if not is_dataclass(data_obj):
        return
    for f in fields(data_obj):
        field_val = getattr(data_obj, f.name)
        if hasattr(field_val, "clear"):
            clear_method = getattr(field_val, "clear")
            if isinstance(clear_method, Callable):
                clear_method()


@dataclass
class LookupTables:
    """Lookup Tables for UUID-to-Entity/Relation"""

    entities: dict[str, Entity] = field(default_factory=dict)
    relations: dict[str, Relation] = field(default_factory=dict)
    episodes: dict[str, Episode] = field(default_factory=dict)

    def get_entity(
        self,
        input_obj: Mapping,
    ) -> Entity:
        """Get entity in duplication-safe manner"""
        entity_id = input_obj["uuid"]
        entity = self.entities.get(entity_id)
        if entity is None:
            self.entities[entity_id] = entity = Entity(**input_obj)
        return entity

    def get_relation(self, input_obj: Mapping) -> Relation:
        """Get relation in duplication-safe manner"""
        relation_id = input_obj["uuid"]
        relation = self.relations.get(relation_id)
        if relation is None:
            self.relations[relation_id] = relation = Relation(**input_obj)
        return relation

    def get_episode(self, input_obj: Mapping) -> Episode:
        """Get episode in duplication-safe manner"""
        episode_id = input_obj["uuid"]
        episode = self.episodes.get(episode_id)
        if episode is None:
            self.episodes[episode_id] = episode = Episode(**input_obj)
        return episode

    def clear(self):
        """Clear references to prevent memory leak"""
        nested_clear_dataclass(self)


@dataclass
class EntityMerge:
    """Lookup Tables for UUID-to-Entity/Relation"""

    target: Entity
    source: dict[str, Entity] = field(default_factory=dict)
    new_relations: list[Relation] = field(default_factory=list)
    relations_to_keep: set[str] = field(default_factory=set)

    def clear(self):
        """Clear references to prevent memory leak"""
        nested_clear_dataclass(self)


@dataclass
class GraphMemUpdate:
    """Graph Memory Update"""

    added_episode: list[Episode] = field(default_factory=list)
    updated_episode: list[Episode] = field(default_factory=list)
    added_entity: list[Entity] = field(default_factory=list)
    updated_entity: list[Entity] = field(default_factory=list)
    added_relation: list[Relation] = field(default_factory=list)
    updated_relation: list[Relation] = field(default_factory=list)
    removed_entity: set[str] = field(default_factory=set)
    removed_relation: set[str] = field(default_factory=set)

    def __or__(self, other: "GraphMemUpdate") -> "GraphMemUpdate":
        new_obj = GraphMemUpdate()
        for field_obj in fields(self):
            attr = field_obj.name
            val = getattr(self, attr)
            if isinstance(val, set):
                val = val | getattr(other, attr)
            else:
                val = val + getattr(other, attr)
            setattr(new_obj, attr, val)
        return new_obj


@dataclass
class GraphMemPrompting:
    """Schema Definitions for Graph Memory Addition"""

    # Output Schema
    schema_entity_extraction: dict[str, Any] = field(default_factory=EntitySummary.response_format)
    schema_entity_dedupe: dict[str, Any] = field(default_factory=EntityDuplication.response_format)
    schema_relation_merge: dict[str, Any] = field(default_factory=MergeRelations.response_format)
    schema_relation_filter: dict[str, Any] = field(default_factory=RelevantFacts.response_format)

    # Prompt Language
    language: str = field(default="cn")
    entity_extraction_language: str = field(default="cn")
    relation_extraction_language: str = field(default="cn")
    entity_dedupe_language: str = field(default="cn")

    def clear(self):
        """Clear references to prevent memory leak"""
        nested_clear_dataclass(self)


@dataclass
class GraphMemState:
    """Current State of Graph Memory Addition"""

    # Task buffers (deferred update or concurrency)
    tasks: list[asyncio.Future] = field(default_factory=list)
    merging_tasks: list[asyncio.Future] = field(default_factory=list)
    merging_tasks_entities: dict[asyncio.Future, Entity] = field(default_factory=dict)
    pending_merge: dict[str, asyncio.Future] = field(default_factory=dict)
    relation_deferred_updates: dict[str, list[tuple[Relation, str, str]]] = field(default_factory=dict)
    relation_filter_tasks: dict[asyncio.Future, tuple[Entity, list[Relation]]] = field(default_factory=dict)

    # General-purpose temporary buffers
    to_remove: list[BaseGraphObject | str] = field(default_factory=list)
    tmp_buffer: list = field(default_factory=list)

    # Special-purpose temporary buffers
    updated_entities_in_current_ep: list[Entity] = field(default_factory=list)
    retrieved_entities: dict[str, Entity] = field(default_factory=dict)
    retrieved_relations: dict[str, Relation] = field(default_factory=dict)
    faulty_relations: dict[str, Relation] = field(default_factory=dict)
    merge_infos: dict[str, EntityMerge] = field(default_factory=dict)

    # Changes to memory (accumulate to flush all at the end for safety)
    mem_update: GraphMemUpdate = field(default_factory=GraphMemUpdate)
    mem_update_skip_embed: GraphMemUpdate = field(default_factory=GraphMemUpdate)

    # Shared variables / dictionaries
    current_timestamp: int = field(default_factory=get_current_utc_timestamp)
    reference_timestamp: int = field(default=0)
    lookup_table: LookupTables = field(default_factory=LookupTables)
    extras: dict[str, Any] = field(default_factory=dict)
    strategy: AddMemStrategy = field(default_factory=AddMemStrategy)
    prompting: GraphMemPrompting = field(default_factory=GraphMemPrompting)
    entity_types: list[EntityDef] = field(default_factory=list)
    episode_type: EpisodeType = field(default=EpisodeType.CONVERSATION)
    content: str = field(default="")
    history: str = field(default="")

    def clear_references(self):
        """Clear references to prevent memory leak"""
        for merge_info in self.merge_infos.values():
            nested_clear_dataclass(merge_info)
        nested_clear_dataclass(self)


@contextlib.contextmanager
def block_keyboard_interrupt():
    """Try to finish all database operations even if user ctrl+c"""

    def tmp_handler(sig, frame):
        memory_logger.warning("Graph Memory: received SIGINT from user, but critical database operation is in progress")
        received["flag"] = True

    received = {"flag": False}
    old_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, tmp_handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, old_handler)
        if received["flag"]:
            raise KeyboardInterrupt


async def batch_embed(
    data: list[BaseGraphObject], embedding_service: Embedding, config: GraphConfig
) -> list[BaseGraphObject]:
    """Embed graph objects in batches"""

    if not data:
        return []

    # Fetch all embedding tasks in the form of (obj, attribute, value) tuples
    embed_task_metadata: list[tuple[object, str, Any]] = []
    for graph_object in data:
        embed_task_metadata.extend(graph_object.fetch_embed_task())

    if not embed_task_metadata:
        return []

    try:
        # Perform embedding tasks and set respective attributes
        embed_result = await embedding_service.embed_documents(
            [task_tuple[-1] for task_tuple in embed_task_metadata],
            batch_size=config.embed_batch_size,
        )
        for (obj, attribute, _), embedding in zip(embed_task_metadata, embed_result):
            setattr(obj, attribute, embedding)
        return []
    except Exception:
        return list(data)


async def persist_to_db(db_backend: GraphStore, state: GraphMemState, config: GraphConfig):
    """Persist data to database in a safe manner"""

    # For safety, validate mem_update_skip_embed.updated_entity first
    state.tmp_buffer.clear()
    for entity in state.mem_update_skip_embed.updated_entity:
        if entity.content_embedding is None or entity.name_embedding is None:
            state.tmp_buffer.append(entity)
            if entity not in state.mem_update.updated_entity:
                state.mem_update.updated_entity.append(entity)
    for entity in state.tmp_buffer:
        while entity in state.mem_update_skip_embed.updated_entity:
            state.mem_update_skip_embed.updated_entity.remove(entity)

    # Try to embed new entity, new relation and updated entity
    graph_objects_to_embed = (
        state.mem_update.added_entity + state.mem_update.added_relation + state.mem_update.updated_entity
    )
    retries = config.request_max_retries
    while retries > 0:
        graph_objects_to_embed = await batch_embed(graph_objects_to_embed, db_backend.embedder, config)
        if not graph_objects_to_embed:
            break
        retries -= 1
    else:
        raise build_error(
            StatusCode.MEMORY_GRAPH_EMBEDDING_CALL_FAILED,
            error_msg="Unable to access embedding service",
        )

    # Try to embed new episode
    graph_objects_to_embed = state.mem_update.added_episode
    retries = config.request_max_retries
    while retries > 0:
        graph_objects_to_embed = await batch_embed(graph_objects_to_embed, db_backend.embedder, config)
        if not graph_objects_to_embed:
            break
        # Maybe episode
        episode = state.mem_update.added_episode[0]
        episode.content = episode.content[: len(episode.content) // 2]
        retries -= 1
    else:
        raise build_error(
            StatusCode.MEMORY_GRAPH_EMBEDDING_CALL_FAILED,
            error_msg="Unable to access embedding service for new episode, maybe exceeding context limit",
        )

    with block_keyboard_interrupt():
        await db_backend.add_entity(entities=state.mem_update.added_entity, upsert=False, flush=False, no_embed=True)
        await db_backend.add_relation(
            relations=state.mem_update.added_relation, upsert=False, flush=False, no_embed=True
        )
        await db_backend.add_episode(episodes=state.mem_update.added_episode, upsert=False, flush=False, no_embed=True)
        await db_backend.add_entity(entities=state.mem_update.updated_entity, upsert=True, flush=False, no_embed=True)

        if state.mem_update_skip_embed.updated_episode:
            await db_backend.add_episode(
                episodes=state.mem_update_skip_embed.updated_episode, flush=False, upsert=True, no_embed=True
            )
        if state.mem_update_skip_embed.updated_entity:
            await db_backend.add_entity(
                state.mem_update_skip_embed.updated_entity, flush=False, upsert=True, no_embed=True
            )
        if state.mem_update_skip_embed.updated_relation:
            await db_backend.add_relation(
                state.mem_update_skip_embed.updated_relation, flush=False, upsert=True, no_embed=True
            )
        if state.mem_update.removed_entity:
            await db_backend.delete(collection=ENTITY_COLLECTION, ids=list(state.mem_update.removed_entity))
        if state.mem_update.removed_relation:
            await db_backend.delete(collection=RELATION_COLLECTION, ids=list(state.mem_update.removed_relation))


def classify_relations_extracted(relations: list[Relation], state: GraphMemState):
    """Classify relations due to entity merging"""
    # Classify relations to keep & remove
    for merge_info in state.merge_infos.values():
        for relation in merge_info.new_relations:
            lhs_uuid = getattr(relation.lhs, "uuid", relation.lhs)
            rhs_uuid = getattr(relation.rhs, "uuid", relation.rhs)
            if lhs_uuid != rhs_uuid:
                merge_info.relations_to_keep.add(relation.uuid)
            else:
                state.mem_update.removed_relation.add(relation.uuid)
        merge_info.target.relations = list(merge_info.relations_to_keep.union(merge_info.target.relations))
    # Classify relations extracted
    state.tmp_buffer.clear()  # relations to embed
    for relation in relations:
        # Record self-pointing relations (fact about object)
        relation.language = state.prompting.language
        if not relation.content.strip():
            state.to_remove.append(relation)
        elif relation.lhs == relation.rhs:
            content = relation.lhs.content.removesuffix("\n")
            relation.lhs.content = f"{content}\n- {relation.content}"
            state.to_remove.append(relation)
        else:
            state.tmp_buffer.append(relation.content)  # used in GraphMemory._relation_dedupe
