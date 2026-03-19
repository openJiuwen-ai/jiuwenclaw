# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph memory store and retrieval.

This module provides GraphMemory, which maintains a knowledge graph over user
conversations and documents: it extracts entities and relations via LLM,
merges and deduplicates them, and supports semantic search over entities,
relations, and episodes.
"""

import asyncio
import datetime
import gc
import random
import threading
import time
from math import ceil
from typing import Literal, Optional, Union

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, BaseMessage
from openjiuwen.core.foundation.prompt.template import PromptTemplate
from openjiuwen.core.foundation.store.base_embedding import Embedding
from openjiuwen.core.foundation.store.base_reranker import Reranker
from openjiuwen.core.foundation.store.graph import (
    ENTITY_COLLECTION,
    EPISODE_COLLECTION,
    RELATION_COLLECTION,
    Entity,
    Episode,
    GraphConfig,
    GraphStoreFactory,
    Relation,
)
from openjiuwen.core.foundation.store.graph.graph_object import BaseGraphObject
from openjiuwen.core.foundation.store.graph.result_ranking import WeightedRankConfig
from openjiuwen.core.foundation.store.graph.utils import (
    format_list_of_messages,
    format_timestamp,
    safe_timestamp,
    with_metadata,
)
from openjiuwen.core.foundation.store.query import base as query_expr
from openjiuwen.core.memory.config.graph import (
    DEFAULT_STRATEGY,
    AddMemStrategy,
    EpisodeType,
    SearchConfig,
)
from openjiuwen.core.memory.graph.extraction import extraction_prompts
from openjiuwen.core.memory.graph.extraction.entity_type_definition import AIEntity, EntityDef, HumanEntity
from openjiuwen.core.memory.graph.extraction.extraction_models import (
    EntityDeclaration,
    EntityDuplication,
    EntitySummary,
    MergeRelations,
    RelationExtraction,
    RelevantFacts,
    TimezonePredictions,
)
from openjiuwen.core.memory.graph.extraction.parse_response import ensure_list, parse_json
from openjiuwen.core.memory.graph.extraction.prompts.entity_extraction.base import ensure_valid_language

from .parse_llm_response import parse_all_relations, resolve_entities
from .postprocess_graph_objects import (
    create_episode,
    parse_relation_uuids_to_remove,
    process_entities,
    process_relations,
    validate_entities_episodes,
)
from .states import EntityMerge, GraphMemState, GraphMemUpdate, classify_relations_extracted, persist_to_db
from .utils import assemble_invoke_params, msg2dict, update_entity
from .validate_input import validate_add_memory_input, validate_search_input

_STORE_TYPE = "graph mem store"


class GraphMemory:
    """Graph memory that handles addition and retrieval of knowledge graph memory.

    Manages entities, relations, and episodes: extracts them from content via LLM,
    merges/deduplicates with existing graph data, and supports configurable search
    over entities, relations, and episodes with optional reranking.
    """

    def __init__(
        self,
        db_config: GraphConfig,
        llm_client: Optional[Model] = None,
        llm_structured_output: bool = True,
        reranker: Optional[Reranker] = None,
        extraction_strategy: AddMemStrategy = DEFAULT_STRATEGY,
        db_kwargs: Optional[dict] = None,
        llm_extra_kwargs: Optional[dict] = None,
        language: Literal["cn", "en"] = "cn",
        debug: bool = False,
    ):
        """Initialize graph memory with backend and extraction settings.

        Args:
            db_config: Graph store configuration (storage, collections, etc.).
            llm_client: Optional LLM client used for entity/relation extraction and
                merging. Defaults to None (must be set or supplied per-call if needed).
            llm_structured_output: Whether to request structured JSON output from
                the LLM. Defaults to True.
            reranker: Optional cross-encoder reranker for search when a strategy
                enables rerank. Defaults to None.
            extraction_strategy: Strategy for recall, merge, and language of
                extraction prompts. Defaults to DEFAULT_STRATEGY.
            db_kwargs: Optional extra keyword arguments passed to the graph store
                factory when creating the backend. Defaults to None.
            llm_extra_kwargs: Optional dict of extra arguments merged into every
                LLM invoke (e.g. temperature). Defaults to None.
            language: Default language for prompts and content ("cn" or "en").
                Defaults to "cn".
            debug: If True, log template names and LLM query/response for
                debugging. Defaults to False.
        """
        # set default values
        db_kwargs = db_kwargs or dict()

        self.token_record: dict[str, int] = dict(input_tokens=0, output_tokens=0)
        self.default_extraction_strategy = extraction_strategy
        self.reranker = reranker
        self.language = ensure_valid_language(language, db_config.db_storage_config.language)
        self.db_backend = GraphStoreFactory.from_config(config=db_config, **db_kwargs)
        self.config = db_config
        self.llm_client = llm_client
        self.llm_extra_kwargs = llm_extra_kwargs
        self.llm_structured_output = llm_structured_output
        self.thread_lock = threading.Lock()
        self.user_locks: dict[str, threading.Lock] = dict()
        self.debug = debug
        self.time_till_next_gc = 300
        self.metric_is_sim = self.db_backend.return_similarity_score
        self._search_strategies: dict[str, tuple[SearchConfig, SearchConfig, SearchConfig]] = dict(
            default=(
                SearchConfig(rank_config=WeightedRankConfig()),
                SearchConfig(min_score=0.1),
                SearchConfig(min_score=0.1),
            )
        )
        self._last_gc: float = time.time()
        concurrency_limit = self.db_backend.config.max_concurrent or 8
        self._semaphore = asyncio.Semaphore(concurrency_limit)

    @property
    def embedder(self) -> Embedding:
        """Embedding used by the graph backend for entities, relations, and episodes."""
        return self.db_backend.embedder

    def attach_embedder(self, embedder: Embedding):
        """Set the embedding used by the graph backend for indexing and search."""
        self.db_backend.attach_embedder(embedder)

    def attach_reranker(self, reranker: Reranker):
        """Set the cross-encoder reranker used when a search strategy has rerank enabled."""
        if isinstance(reranker, Reranker):
            self.reranker = reranker
        else:
            raise build_error(
                StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                store_type=_STORE_TYPE,
                error_msg=f"Reranker must be an implementation of Reranker, got {type(reranker)} instead.",
            )

    def register_search_strategy(
        self,
        name: str,
        search_entity: Optional[SearchConfig] = None,
        search_relation: Optional[SearchConfig] = None,
        search_episode: Optional[SearchConfig] = None,
        force: bool = False,
    ):
        """Register a named search strategy with configs for entity, relation, and episode search.

        Args:
            name: Strategy name (e.g. "default"); used in search(..., search_strategy=name).
            search_entity: Config for entity collection search, or None to use default.
            search_relation: Config for relation collection search, or None to use default.
            search_episode: Config for episode collection search, or None to use default.
            force: If True, overwrite an existing strategy with the same name.

        Raises error:
            - name is empty
            - name already exists and force is False
            - any config is not a SearchConfig instance or None.
        """
        input_configs = [search_entity, search_relation, search_episode]
        if not all(arg is None or isinstance(arg, SearchConfig) for arg in input_configs):
            raise build_error(
                StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                store_type=_STORE_TYPE,
                error_msg="Search config for entity/relation/episode must be an instance of SearchConfig or None",
            )

        with self.thread_lock:
            if not name:
                raise build_error(
                    StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                    store_type=_STORE_TYPE,
                    error_msg="Search config cannot be registered as an empty value.",
                )
            if name in self._search_strategies and not force:
                raise build_error(
                    StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                    store_type=_STORE_TYPE,
                    error_msg=f"Search config with name [{name}] already exists.",
                )

            self._search_strategies[name] = (
                search_entity or SearchConfig(rank_config=WeightedRankConfig()),
                search_relation or SearchConfig(min_score=0.1),
                search_episode or SearchConfig(min_score=0.1),
            )

    def ensure_thread_lock(self, user_id: str):
        """Create and store a per-user lock if not present, so add_memory is serialized per user."""
        with self.thread_lock:
            if user_id not in self.user_locks:
                self.user_locks[user_id] = threading.Lock()

    async def add_memory(
        self,
        src_type: EpisodeType,
        user_id: str,
        content: list[BaseMessage | dict] | str,
        content_fmt_kwargs: Optional[dict] = None,
        reference_time: Optional[datetime.datetime] = None,
    ) -> GraphMemUpdate:
        """Add memory episode to Memory Knowledge Graph

        Args:
            src_type (EpisodeType): type of episode: conversation/document/json.
            user_id (str): user id.
            content (list[BaseMessage | dict] | str): content of episode.
            content_fmt_kwargs (Optional[dict], optional): formatting arguments like {"user": "张三（用户）",\
                "assistant": "智能客服小李"}. Defaults to None.
            reference_time (Optional[datetime.datetime], optional): reference time for when the episode takes place,\
                leave blank will use current time. Defaults to None.

        Returns:
            GraphMemUpdate: returns the memory update details
        """
        self.ensure_thread_lock(user_id=user_id)
        if not self.embedder:
            raise build_error(
                StatusCode.MEMORY_GRAPH_EMBED_MODEL_NOT_FOUND,
                error_msg="use the attach_embedder method to attach one",
            )
        with self.user_locks[user_id]:
            state = self._init_state(reference_time)

            content = await self._prepare_episodes(
                src_type,
                user_id,
                content,
                state,
                content_fmt_kwargs,
            )
            current_episode = await create_episode(self.db_backend, user_id, content, state)
            content = format_timestamp(state.reference_timestamp) + "\n" + content

            # Timezone Predictions
            tz_task = asyncio.create_task(
                self._invoke_llm(
                    *extraction_prompts.extract_timezone(
                        content=content, history=state.history, language=state.prompting.language
                    ),
                )
            )

            # Extract Entity Declarations
            no_existing_entity, extracted_declarations = await self._extract_entity_declarations(
                src_type, content, state
            )

            # Extract Relations
            response = await tz_task
            state.tasks.append(
                asyncio.create_task(
                    self._invoke_llm(
                        *extraction_prompts.extract_relation_declaration(
                            relation_types=None,
                            entities=extracted_declarations,
                            reference_time=state.reference_timestamp,
                            tz_info=parse_json(
                                response.content,
                                output_schema=TimezonePredictions.response_format(state.prompting.language),
                            )
                            or [],
                            entity_types=state.entity_types,
                            content=content,
                            history=state.history,
                            language=state.prompting.relation_extraction_language,
                        ),
                    )
                )
            )

            # Find relevant existing entities
            await self._fetch_relevant_entities(extracted_declarations, no_existing_entity, user_id, state)

            # Entity merging
            existing_entities_list: list[dict] = [entity.model_dump() for entity in state.retrieved_entities.values()]
            if existing_entities_list:
                state.tasks.append(
                    asyncio.create_task(
                        self._invoke_llm(
                            *extraction_prompts.dedupe_entity_list(
                                content,
                                candidate_entities=extracted_declarations,
                                existing_entities=existing_entities_list,
                                entity_types=state.entity_types,
                                history=state.history,
                                language=state.prompting.entity_dedupe_language,
                            )
                        )
                    )
                )
            extracted_declarations = await self._entity_merge(extracted_declarations, existing_entities_list, state)

            response = await state.tasks.pop(0)
            relations, entities = parse_all_relations(
                ensure_list(
                    parse_json(
                        response.content,
                        output_schema=RelationExtraction.response_format(state.prompting.relation_extraction_language),
                    )
                    or []
                ),
                entities=extracted_declarations,
                entity_types=state.entity_types,
                created_at=state.reference_timestamp,
                user_id=user_id,
            )

            # Extract summary & attribute for entities
            entities = await self._entity_enrich(entities, content, state)
            # Parse relation filtering result
            await self._parse_relation_filtering_result(relations, state)
            # Bulk-embed relations & de-duplication
            await self._handle_relation_dedupe(user_id, content, relations, state)
            await self._update_entities_for_relation_removal(state, extracted_declarations)

            # Post-process relations & entities, then persist to database!
            await process_relations(self.db_backend, entities, relations, state)
            await process_entities(self.db_backend, entities, current_episode, state)
            validate_entities_episodes(entities, current_episode, state)
            await persist_to_db(self.db_backend, state, self.db_backend.config)
            # Clean up resources
            state.clear_references()
            del relations, entities, current_episode, response, existing_entities_list
            del extracted_declarations
            await self.db_backend.refresh(skip_compact=True)

        # Check if garbage collection should be manually invoked
        with self.thread_lock:
            if self.time_till_next_gc >= 0:
                if time.time() - self._last_gc > self.time_till_next_gc:
                    gc.collect()
                    self._last_gc = time.time()
                    await self.db_backend.refresh(skip_compact=False)

        return state.mem_update | state.mem_update_skip_embed

    async def search(
        self,
        query: str,
        user_id: Union[str, list[str]],
        search_strategy: str = "default",
        *,
        entity: bool = True,
        relation: bool = True,
        episode: bool = True,
        query_embedding: Optional[list[float]] = None,
    ) -> dict[str, list[tuple[float, BaseGraphObject]]]:
        """Search the graph by query across entities, relations, and/or episodes.

        Args:
            query: Natural language or text query.
            user_id: Single user id or list of user ids to restrict results.
            search_strategy: Registered strategy name (e.g. "default").
            entity: Whether to search entity collection. Defaults to True.
            relation: Whether to search relation collection. Defaults to True.
            episode: Whether to search episode collection. Defaults to True.
            query_embedding: Optional precomputed query embedding; if None, embedder is used.

        Returns:
            Dict mapping collection name ("entity", "relation", "episode") to list of
            (score, graph object) tuples.
        """
        if search_strategy not in self._search_strategies:
            if not (isinstance(search_strategy, str) and search_strategy.strip()):
                raise build_error(
                    StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                    store_type=_STORE_TYPE,
                    error_msg="strategy must be a non-empty string value",
                )
            raise build_error(
                StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                store_type=_STORE_TYPE,
                error_msg=f"Strategy [{search_strategy}] not found, please register with register_search_configs "
                'method or use "default".',
            )
        user_id = validate_search_input(query, user_id, [entity, relation, episode])
        if query_embedding is None:
            if not self.embedder:
                raise build_error(
                    StatusCode.MEMORY_GRAPH_EMBED_MODEL_NOT_FOUND,
                    error_msg="use the attach_embedder method to attach one",
                )
            query_embedding = await self.db_backend.embedder.embed_query(query)
        elif not (isinstance(query_embedding, list) and all(isinstance(val, float) for val in query_embedding)):
            raise build_error(
                StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                store_type=_STORE_TYPE,
                error_msg="query_embedding must be a list[float] or None",
            )
        tasks, result = [], {}

        if entity:
            self._perform_search(0, user_id, search_strategy, tasks, dict(query=query, query_embedding=query_embedding))
        if relation:
            self._perform_search(1, user_id, search_strategy, tasks, dict(query=query, query_embedding=query_embedding))
        if episode:
            self._perform_search(2, user_id, search_strategy, tasks, dict(query=query, query_embedding=query_embedding))
        for task in asyncio.as_completed(tasks):
            returned_list, col = await task
            g_obj_cls = dict(ENTITY_COLLECTION=Entity, RELATION_COLLECTION=Relation, EPISODE_COLLECTION=Episode)[col]
            result[col] = [
                (returned_dict.pop("distance", 0.0), g_obj_cls(**returned_dict)) for returned_dict in returned_list
            ]
        return result

    @staticmethod
    def _replace_one_side_of_relation(
        side: str,
        relation: Relation,
        tgt_uuid: str,
        entity_relation_updates: dict[str, dict[str, Relation]],
        state: GraphMemState,
    ):
        """Update one side (lhs or rhs) of a relation to point to target entity, or mark as faulty if duplicate."""
        if relation.uuid not in entity_relation_updates[tgt_uuid]:
            state.relation_deferred_updates[tgt_uuid].append((relation, side, tgt_uuid))
            entity_relation_updates[tgt_uuid][relation.uuid] = relation
        else:
            state.faulty_relations[relation.uuid] = relation
            del entity_relation_updates[tgt_uuid][relation.uuid]
            for task in [task for task in state.relation_deferred_updates[tgt_uuid] if task[0] == relation]:
                state.relation_deferred_updates[tgt_uuid].remove(task)

    @staticmethod
    async def _parse_relation_filtering_result(relations: list[Relation], state: GraphMemState):
        """Wait for relation-filter LLM tasks, apply kept relations to merge infos, and classify relations."""
        if state.relation_filter_tasks:
            await asyncio.wait(state.relation_filter_tasks)
        for task in state.relation_filter_tasks:
            tgt_entity, new_relation_list = state.relation_filter_tasks[task]
            tgt_uuid = tgt_entity.uuid
            try:
                response = await task
                dedupe_entity = parse_json(response.content, output_schema=state.prompting.schema_relation_filter) or {}
                keep_ids = set(dedupe_entity.get("relevant_relations"))
                relations_filtered = [new_relation_list[i - 1] for i in keep_ids]
            except Exception:
                relations_filtered = new_relation_list
            state.merge_infos[tgt_uuid].new_relations = relations_filtered

        for tgt_uuid, merge_info in state.merge_infos.items():
            for relation, attr, val in state.relation_deferred_updates[tgt_uuid]:
                if relation in merge_info.new_relations:
                    setattr(relation, attr, val)
                    if relation not in state.mem_update_skip_embed.updated_relation:
                        state.mem_update_skip_embed.updated_relation.append(relation)
                else:
                    state.mem_update.removed_relation.add(relation.uuid)
                    state.to_remove.append(relation)

        classify_relations_extracted(relations, state)

    async def _invoke_llm(
        self, kwargs: dict, template: PromptTemplate, output_model: Optional[dict] = None, **extra
    ) -> AssistantMessage:
        """Helper method for graph memory to easily invoke LLM clients

        Args:
            kwargs (dict): Keyword arguments to fill into prompt template.
            template (PromptTemplate): Prompt template to use.
            output_model (Optional[dict], optional): Response format for structured output. Defaults to None.
            **extra: Extra arguments to supply in request, such as enable_thinking=False.

        Returns:
            AssistantMessage: LLM response.
        """
        params = assemble_invoke_params(kwargs, template, output_model if self.llm_structured_output else None)
        if self.llm_extra_kwargs is not None:
            params.update(self.llm_extra_kwargs)
        params.update(extra)
        should_raise_error = [False] * (self.config.request_max_retries - 1) + [True]  # only raise error on last retry
        async with self._semaphore:
            for raise_exception in should_raise_error:
                try:
                    response = await self.llm_client.invoke(**params)
                    break
                except Exception as e:
                    if raise_exception:
                        raise build_error(
                            StatusCode.MEMORY_GRAPH_INVOKE_LLM_FAILED,
                            error_msg=str(e),
                            cause=e,
                        ) from e
                    memory_logger.error("Graph Memory LLM Invoke Error: %s", e)
                    await asyncio.sleep(random.random() / 2)
        if self.debug:
            sep = f"\n{'=' * 60}\n"
            query = params.get("messages", [{}])[-1].get("content")
            debug_msg = f"TEMPLATE {template.name}{sep}{query}{sep}{response.content}"
            with self.thread_lock:
                memory_logger.debug("Graph Memory LLM Invoke: %s", debug_msg)
        return response

    def _init_state(self, reference_time: Optional[datetime.datetime] = None) -> GraphMemState:
        """Create a GraphMemState for the current add_memory request with prompting and strategy set."""
        strategy = self.default_extraction_strategy

        state = GraphMemState(strategy=strategy, entity_types=[EntityDef(), HumanEntity(), AIEntity()])
        state.prompting.language = self.language
        state.prompting.entity_extraction_language = "cn" if strategy.chinese_entity else self.language
        state.prompting.relation_extraction_language = "cn" if strategy.chinese_relation else self.language
        state.prompting.entity_dedupe_language = "cn" if strategy.chinese_entity_dedupe else self.language
        state.prompting.schema_entity_extraction = EntitySummary.response_format(self.language)
        state.prompting.schema_entity_dedupe = EntityDuplication.response_format(state.prompting.entity_dedupe_language)
        state.prompting.schema_relation_merge = MergeRelations.response_format(self.language)
        state.prompting.schema_relation_filter = RelevantFacts.response_format(self.language)
        state.extras = dict(summary_target=str(strategy.summary_target))

        if reference_time is None:
            state.reference_timestamp = state.current_timestamp
        elif isinstance(reference_time, datetime.datetime):
            state.reference_timestamp = int(safe_timestamp(reference_time))
        else:
            raise build_error(
                StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                store_type=_STORE_TYPE,
                error_msg="reference_time must be a valid datetime object",
            )

        return state

    async def _prepare_episodes(
        self,
        src_type: EpisodeType,
        user_id: str,
        content: list[BaseMessage | dict[str, str]] | str,
        state: GraphMemState,
        content_fmt_kwargs: Optional[dict] = None,
    ) -> str:
        """Validate and normalize episode content, retrieve relevant history episodes, and return formatted content."""
        validate_add_memory_input(
            self.db_backend.config.db_storage_config.user_id, src_type, user_id, content_fmt_kwargs
        )
        if isinstance(content, str):
            if content_fmt_kwargs:
                raise build_error(
                    StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                    store_type=_STORE_TYPE,
                    error_msg="content_fmt_kwargs has no effect when content is str, please leave it empty",
                )
        else:
            if src_type == EpisodeType.CONVERSATION:
                try:
                    content = msg2dict(content)
                    if not all((isinstance(msg, dict) and "role" in msg and "content" in msg) for msg in content):
                        raise build_error(
                            StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                            store_type=_STORE_TYPE,
                            error_msg='The content is not a list of dict with keys "role" and "content"',
                        )
                except Exception as e:
                    raise build_error(
                        StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                        store_type=_STORE_TYPE,
                        error_msg="The content must be str or list of messages in dict or BaseMessage",
                        cause=e,
                    ) from e
                content = format_list_of_messages(content, role_replace=content_fmt_kwargs)
            else:
                raise build_error(
                    StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                    store_type=_STORE_TYPE,
                    error_msg="The content must be str when source type is not conversation",
                )
        content = content.strip()
        if not content:
            raise build_error(
                StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                store_type=_STORE_TYPE,
                error_msg="content must be a non-empty value of either a str or a list of messages in "
                "OpenAI (dict[str, str]) / openJiuwen (BaseMessage) standard",
            )

        # Retrieve Relevant Histories
        result: list[Episode] = []
        recall_strategy = state.strategy.recall_episode
        maximize = self.metric_is_sim or recall_strategy.rank_config.higher_is_better
        if recall_strategy.top_k and not self.db_backend.is_empty(EPISODE_COLLECTION):
            # Assemble search query
            query_components = []
            if user_id:
                query_components.append(query_expr.filter_user(user_id))
            if recall_strategy.same_kind:
                query_components.append(query_expr.eq("obj_type", src_type.name))
            if recall_strategy.exclude_future_results:
                query_components.append(query_expr.lte("valid_since", state.reference_timestamp))
            ep_search_query = query_expr.chain_filters(query_components)
            # Retrieve episodes
            result = (
                await self.db_backend.search(
                    content,
                    k=recall_strategy.top_k,
                    collection=EPISODE_COLLECTION,
                    ranker_config=recall_strategy.rank_config,
                    filter_expr=ep_search_query,
                    language=state.prompting.language,
                )
            )[EPISODE_COLLECTION]
            if maximize:
                result = [r for r in result if r.get("distance", 0.0) >= recall_strategy.min_score]
            else:
                result = [r for r in result if r.get("distance", 0.0) <= recall_strategy.min_score]
            result = [Episode(**ep) for ep in result]
            result.sort(key=lambda ep: ep.valid_since)
        for ep in result:
            state.lookup_table.episodes[ep.uuid] = ep
        state.history = "\n---\n".join(format_timestamp(ep.created_at) + "\n" + ep.content for ep in result)
        return content

    def _perform_search(self, col_idx: int, user_id: str, search_strategy: str, tasks: list, kwargs: dict):
        """Schedule a search task for one collection (entity/relation/episode) and append to tasks."""
        names = [ENTITY_COLLECTION, RELATION_COLLECTION, EPISODE_COLLECTION]
        config_e = self._search_strategies[search_strategy][col_idx]
        if config_e.rerank and self.reranker is None:
            raise build_error(
                StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                store_type=_STORE_TYPE,
                error_msg=f"Search strategy [{search_strategy}] for {names[col_idx]} has rerank=True "
                "but reranker is not set, please use the attach_reranker method to attach a reranker.",
            )
        filter_by_user = query_expr.filter_user(user_id)
        config_e = config_e.model_copy()
        config_e.filter_expr = config_e.filter_expr & filter_by_user if config_e.filter_expr else filter_by_user
        search_task = with_metadata(self._search(col=names[col_idx], search_config=config_e, **kwargs), names[col_idx])
        tasks.append(asyncio.create_task(search_task))

    async def _search(self, col: str, query: str, search_config: SearchConfig, query_embedding: Optional[list] = None):
        """Run vector/search on one collection with the given config and return raw results for that collection."""
        return (
            await self.db_backend.search(
                query=query,
                k=search_config.top_k,
                collection=col,
                ranker_config=search_config.rank_config,
                bfs_depth=search_config.bfs_depth,
                bfs_k=search_config.bfs_k,
                filter_expr=search_config.filter_expr,
                output_fields=search_config.output_fields,
                language=search_config.language,
                query_embedding=query_embedding,
                reranker=self.reranker if search_config.rerank else None,
            )
        ).get(col)

    async def _extract_entity_declarations(
        self,
        src_type: EpisodeType,
        content: str,
        state: GraphMemState,
    ) -> tuple[bool, list[Entity | EntityDeclaration]]:
        """Run LLM to extract entity declarations from content; optionally schedule entity embedding task."""
        prompt_entity_extraction = extraction_prompts.extract_entity_declaration(
            src_type=src_type,
            content=content,
            history=state.history,
            entity_types=state.entity_types,
            language=state.prompting.entity_extraction_language,
            extras=state.extras,
        )

        entity_names = {"user", "assistant", "User", "Assistant", "USER", "ASSISTANT"}
        response = await self._invoke_llm(*prompt_entity_extraction)
        extracted_declarations = parse_json(response.content, output_schema=prompt_entity_extraction[-1]) or []

        if isinstance(extracted_declarations, dict):
            extracted_list = next(iter(extracted_declarations.values()))
            if isinstance(extracted_list, dict):
                extracted_declarations = [extracted_list]
            elif isinstance(extracted_list, list):
                extracted_declarations = extracted_list

        if isinstance(extracted_declarations, list):
            extracted_list = []
            for extraction in extracted_declarations:
                name = extraction.pop("name", "")
                if isinstance(name, str):
                    name = name.strip()
                else:
                    name = ""
                if name and name not in entity_names:
                    entity_names.add(name)
                    type_id = next(v for k, v in extraction.items() if isinstance(k, str) and "type" in k.casefold())
                    extracted_list.append(dict(name=name, entity_type_id=type_id))
            extracted_declarations = extracted_list
        else:
            extracted_declarations = []

        extracted_declarations: list[Entity | EntityDeclaration] = [
            EntityDeclaration(**ent) for ent in extracted_declarations if ent.get("name", "").strip()
        ]
        extracted_entity_names = [ent.name for ent in extracted_declarations]

        # Bluk-Embed Name of New Entities
        no_existing_entity = self.db_backend.is_empty(ENTITY_COLLECTION)
        if (not no_existing_entity) and extracted_entity_names:
            state.tasks.append(
                asyncio.create_task(
                    self.embedder.embed_documents(extracted_entity_names, batch_size=self.config.embed_batch_size)
                )
            )
        return no_existing_entity, extracted_declarations

    async def _fetch_relevant_entities(
        self,
        extracted_declarations: list[Entity | EntityDeclaration],
        no_existing_entity: bool,
        user_id: str,
        state: GraphMemState,
    ):
        """Retrieve existing entities that are relevant to the extracted declarations (by embedding and name)."""
        if not no_existing_entity:
            if len(state.tasks) <= 1:
                return
            entity_embed_results: list[list[float]] = await state.tasks.pop(-2)
            state.tasks = state.tasks[-1:]
            for entity, emb in zip(extracted_declarations, entity_embed_results):
                # Search for semantically relevant entities
                entity_type = (
                    state.entity_types[entity.entity_type_id]
                    if entity.entity_type_id < len(state.entity_types)
                    else None
                )
                filter_by_user = query_expr.filter_user(user_id)
                # If not set to only search in same kind/type, then also perform a type-less search
                if not state.strategy.recall_entity.same_kind:
                    result = (
                        await self.db_backend.search(
                            entity.name,
                            k=state.strategy.recall_entity.top_k,
                            collection=ENTITY_COLLECTION,
                            ranker_config=state.strategy.recall_entity.rank_config,
                            filter_expr=filter_by_user,
                            query_embedding=emb,
                            language=state.prompting.language,
                        )
                    )[ENTITY_COLLECTION]
                    if self.metric_is_sim or state.strategy.recall_entity.rank_config.higher_is_better:
                        result = [r for r in result if r.get("distance", 0.0) >= state.strategy.recall_entity.min_score]
                    else:
                        result = [r for r in result if r.get("distance", 0.0) <= state.strategy.recall_entity.min_score]
                    for r in result:
                        state.retrieved_entities[r["uuid"]] = state.lookup_table.get_entity(r)
                # Always perform a typed search
                if entity_type is not None:
                    result = (
                        await self.db_backend.search(
                            entity.name,
                            k=state.strategy.recall_entity.top_k,
                            collection=ENTITY_COLLECTION,
                            ranker_config=state.strategy.recall_entity.rank_config,
                            filter_expr=filter_by_user & query_expr.MatchExpr(field="obj_type", value=entity_type.name),
                            query_embedding=emb,
                            language=state.prompting.language,
                        )
                    )[ENTITY_COLLECTION]
                else:
                    result = []
                if self.metric_is_sim or state.strategy.recall_entity.rank_config.higher_is_better:
                    result = [r for r in result if r.get("distance", 0.0) >= state.strategy.recall_entity.min_score]
                else:
                    result = [r for r in result if r.get("distance", 0.0) <= state.strategy.recall_entity.min_score]
                for r in result:
                    state.retrieved_entities[r["uuid"]] = state.lookup_table.get_entity(r)

                # Match entities with same name
                exact_match = query_expr.MatchExpr(field="name", value=entity.name, match_mode="exact")
                infix_match = query_expr.MatchExpr(field="name", value=entity.name, match_mode="infix")
                result = await self.db_backend.query(
                    collection=ENTITY_COLLECTION,
                    expr=query_expr.filter_user(user_id) & exact_match,
                    limit=ceil(state.strategy.recall_entity.top_k / 2),
                    silence_errors=True,
                ) + await self.db_backend.query(
                    collection=ENTITY_COLLECTION,
                    expr=query_expr.filter_user(user_id) & infix_match,
                    limit=ceil(state.strategy.recall_entity.top_k / 2),
                    silence_errors=True,
                )
                for e in result:
                    state.retrieved_entities[e["uuid"]] = state.lookup_table.get_entity(e)

    async def _resolve_entity_merges(self, merging_args: list[tuple[Entity, list[Entity]]], state: GraphMemState):
        """Resolve relations and episodes for merged entities: remap relation endpoints and dispatch filter tasks."""
        episodes_to_update = set()
        entity_relation_updates: dict[str, dict[str, Relation]] = {}
        map_src2tgt: dict[str, str] = dict()
        for tgt_entity, src_entities in merging_args:
            tgt_uuid = tgt_entity.uuid
            state.merge_infos[tgt_uuid] = EntityMerge(target=tgt_entity, source={e.uuid: e for e in src_entities})
            alias = set(state.merge_infos[tgt_uuid].source.keys())  # UUIDs that point to same entitiy after merging
            alias.add(tgt_uuid)
            state.relation_deferred_updates[tgt_uuid] = []
            entity_relation_updates[tgt_uuid] = {}
            for src_entity in src_entities:
                map_src2tgt[src_entity.uuid] = tgt_uuid
                src_episode_uuids = [ep if isinstance(ep, str) else ep.uuid for ep in src_entity.episodes]
                tgt_entity.episodes.extend(src_episode_uuids)
                tgt_entity.episodes.extend(src_episode_uuids)
                episodes_to_update.update(src_episode_uuids)
                if not src_entity.relations:
                    continue
                await self._resolve_each_relation(
                    tgt_uuid, src_entity, map_src2tgt, entity_relation_updates, state, alias=alias
                )
            tgt_entity.episodes = list(set(tgt_entity.episodes))
            tgt_entity.episodes = list(set(tgt_entity.episodes))

        state.mem_update.removed_relation.update(state.faulty_relations.keys())
        await self._dispatch_entity_merge_tasks(episodes_to_update, entity_relation_updates, state)

    async def _dispatch_entity_merge_tasks(
        self,
        episodes_to_update: set[str],
        entity_relation_updates: dict[str, dict[str, Relation]],
        state: GraphMemState,
    ):
        """Dispatch LLM relation-filter tasks for merge targets and record episodes to update."""
        obj_cache = state.lookup_table
        if state.strategy.merge_filter:
            for tgt_uuid, relation_dict in entity_relation_updates.items():
                tgt_entity = obj_cache.entities[tgt_uuid]
                relation_list = [r for r in relation_dict.values() if r.uuid not in state.faulty_relations]
                state.merge_infos[tgt_uuid].new_relations = relation_list
                prompt_relation_filter = extraction_prompts.filter_relations_for_merge(
                    tgt_entity, relation_list, state.prompting.language, state.extras
                )
                task = asyncio.create_task(self._invoke_llm(*prompt_relation_filter))
                state.relation_filter_tasks[task] = (tgt_entity, relation_list)
        # Finally, update the episodes
        if episodes_to_update:
            query_result = await self.db_backend.query(EPISODE_COLLECTION, ids=list(episodes_to_update))
            episodes = [obj_cache.get_episode(e) for e in query_result]
            state.mem_update_skip_embed.updated_episode.extend(episodes)

    async def _resolve_each_relation(
        self,
        tgt_uuid: str,
        src_entity: Entity,
        map_src2tgt: dict[str, str],
        entity_relation_updates: dict[str, dict[str, Relation]],
        state: GraphMemState = None,
        *,
        alias: Optional[set[str]] = None,
    ):
        """Remap each relation of a source entity to the target entity; mark self-pointing or invalid as faulty."""
        if alias is None:
            alias = set()
        self_pointing: set[str] = set()
        query_result = await self.db_backend.query(RELATION_COLLECTION, ids=src_entity.relations)
        src_relations = [state.lookup_table.get_relation(r) for r in query_result]
        for relation in src_relations:
            to_replace = src_entity.uuid
            lhs_rhs = {relation.lhs, relation.rhs}
            # Self-pointing -> remove it!
            if all(e_uuid in alias for e_uuid in lhs_rhs):
                state.faulty_relations[relation.uuid] = relation
                self_pointing.add(relation.uuid)
                to_replace = None
            # Should always terminate within one iteration, while loop for safety
            while to_replace in map_src2tgt and relation.uuid not in state.faulty_relations:
                if relation.lhs == to_replace:
                    self._replace_one_side_of_relation("lhs", relation, tgt_uuid, entity_relation_updates, state)
                    break
                if relation.rhs == to_replace:
                    self._replace_one_side_of_relation("rhs", relation, tgt_uuid, entity_relation_updates, state)
                    break
                to_replace = map_src2tgt[to_replace]
            else:
                if relation.uuid not in self_pointing:
                    relation_repr = f"[{relation.lhs}]-<{relation.uuid}>-[{relation.rhs}]"
                    memory_logger.warning(
                        "Graph Memory: relation [%s] not connected to entity [%s] (caught remapping to -> [%s])\n%s",
                        relation.uuid,
                        src_entity.uuid,
                        tgt_uuid,
                        relation_repr,
                    )
                    state.faulty_relations[relation.uuid] = relation

    async def _entity_merge(
        self, extracted_declarations: list, existing_entities_list: list[dict], state: GraphMemState
    ) -> list:
        """Resolve entity dedupe LLM result, compute merge args, run merge tasks and relation resolution."""
        if state.tasks:
            await asyncio.wait(state.tasks)
        if existing_entities_list:
            response_resolve_entity = await state.tasks.pop()
            existing_entities = [state.lookup_table.get_entity(entity_dict) for entity_dict in existing_entities_list]
            dedupe_entity = (
                parse_json(response_resolve_entity.content, output_schema=state.prompting.schema_entity_dedupe) or []
            )
            dedupe_entity = ensure_list(dedupe_entity)

            # If existing entities need merging:
            # - merging_args contains argument tuples for merging tasks: [(target, [source_1, source_2, ...]), ...]
            # - entity_uuids_to_remove contains uuid of entities to remove due to merging
            extracted_declarations, merging_args, entity_uuids_to_remove = resolve_entities(
                extracted_declarations, existing_entities, dedupe_entity
            )
            if not state.strategy.merge_entities:
                merging_args.clear()
            else:
                state.mem_update.removed_entity.update(entity_uuids_to_remove)
            non_blocking_tasks = []  # Merging tasks that would not block entity summary & attribute extractions
            # Dispatch the blocking tasks first
            for tgt, src_entities in merging_args:
                prompt_entity_merge = extraction_prompts.merge_existing_entities(
                    tgt, src_entities, language=state.prompting.language
                )
                if tgt in extracted_declarations:
                    # Submit tasks that would block entity summary / attribute extraction
                    task = asyncio.create_task(self._invoke_llm(*prompt_entity_merge))
                    state.pending_merge[tgt.uuid] = task
                    state.merging_tasks.append(task)
                    state.merging_tasks_entities[task] = tgt
                else:
                    # Non blocking tasks can wait
                    non_blocking_tasks.append((tgt, prompt_entity_merge))
            # Then the non-blocking tasks
            for tgt, prompt_entity_merge in non_blocking_tasks:
                task = asyncio.create_task(self._invoke_llm(*prompt_entity_merge))
                state.merging_tasks.append(task)
                state.merging_tasks_entities[task] = tgt

            # Resolve relations after the entity merges
            await self._resolve_entity_merges(merging_args, state)
        return extracted_declarations

    async def _entity_enrich(self, entities: list[Entity], content: str, state: GraphMemState) -> list[Entity]:
        """Extract summary and attributes for each entity via LLM; wait for pending merges for blocking entities."""
        state.tasks.clear()
        # Classify entities
        entities_blocking: list[Entity] = []
        entities_non_blocking: list[Entity] = []
        for entity in entities:
            if entity.uuid in state.pending_merge:
                entities_blocking.append(entity)
            else:
                entities_non_blocking.append(entity)
        entities = entities_non_blocking + entities_blocking

        # Start non-blocking tasks first
        for entity in entities_non_blocking:
            prompt_entity_summary = extraction_prompts.extract_entity_attributes(
                entity=entity,
                content=content,
                history=state.history,
                language=state.prompting.language,
                extras=state.extras,
            )
            state.tasks.append(asyncio.create_task(self._invoke_llm(*prompt_entity_summary)))

        # Blocking tasks needs to wait
        for entity in entities_blocking:
            task = state.pending_merge[entity.uuid]
            response = await task
            state.merging_tasks.remove(task)
            update_entity(entity, response.content, state.prompting.schema_entity_extraction)
            prompt_entity_summary = extraction_prompts.extract_entity_attributes(
                entity=entity,
                content=content,
                history=state.history,
                language=state.prompting.language,
                extras=state.extras,
            )
            state.tasks.append(asyncio.create_task(self._invoke_llm(*prompt_entity_summary)))
        if state.tasks:
            await asyncio.wait(state.tasks)

        # Update entities
        for entity, future in zip(entities, state.tasks):
            response = await future
            update_entity(entity, response.content, state.prompting.schema_entity_extraction)
        state.tasks.clear()
        return entities

    async def _handle_relation_dedupe(
        self,
        user_id: str,
        content: str,
        relations: list[Relation],
        state: GraphMemState,
    ):
        """Embed relation content, find similar existing relations, and run LLM dedupe to decide keep/remove."""
        # Filter out self-pointing relations (fact about object)
        for relation in state.to_remove:
            if relation in relations:
                relations.remove(relation)
        # Bulk-embed & de-duplicate
        if state.strategy.merge_relations and state.tmp_buffer and not self.db_backend.is_empty(RELATION_COLLECTION):
            results = await self.embedder.embed_documents(state.tmp_buffer, batch_size=self.config.embed_batch_size)
            await self._relation_dedupe(user_id, content, relations, results, state)

    async def _relation_dedupe(
        self,
        user_id: str,
        content: str,
        relations: list[Relation],
        relation_embed_results: list[list[float]],
        state: GraphMemState,
    ):
        """Run relation recall + LLM dedupe per relation and record which relation UUIDs to remove in state."""
        state.tasks.clear()
        dedupe_relation_tasks = []
        for relation, emb in zip(relations, relation_embed_results):
            current_relations: list[Relation] = []
            lhs_rhs = [
                e if isinstance(e, str) else (e.uuid if e.content.strip() else None)
                for e in (relation.lhs, relation.rhs)
            ]
            if not all(lhs_rhs):
                continue
            result = (
                await self.db_backend.search(
                    relation.content,
                    k=state.strategy.recall_relation.top_k,
                    collection=RELATION_COLLECTION,
                    ranker_config=state.strategy.recall_relation.rank_config,
                    filter_expr=query_expr.in_list("lhs", lhs_rhs)
                    & query_expr.in_list("rhs", lhs_rhs)
                    & query_expr.filter_user(user_id),
                    query_embedding=emb,
                    language=state.prompting.language,
                )
            )[RELATION_COLLECTION]
            if self.metric_is_sim or state.strategy.recall_relation.rank_config.higher_is_better:
                result = [r for r in result if r.get("distance", 0.0) >= state.strategy.recall_relation.min_score]
            else:
                result = [r for r in result if r.get("distance", 0.0) <= state.strategy.recall_relation.min_score]

            for r in result:
                relation = state.retrieved_relations[r["uuid"]] = state.lookup_table.get_relation(r)
                current_relations.append(relation)

            if current_relations:
                prompt_relation_dedupe = extraction_prompts.dedupe_relation_list(
                    content=content,
                    relation=relation,
                    existing_relations=current_relations,
                    existing_entities=[relation.lhs.model_dump(), relation.rhs.model_dump()],
                    history=state.history,
                    language=state.prompting.language,
                )
                state.tasks.append(asyncio.create_task(self._invoke_llm(*prompt_relation_dedupe)))
                dedupe_relation_tasks.append((relation, current_relations, state.tasks[-1]))
        if state.tasks:
            await asyncio.wait(state.tasks)
            state.tasks.clear()
        await parse_relation_uuids_to_remove(dedupe_relation_tasks, state)

    async def _update_entities_for_relation_removal(
        self,
        state: GraphMemState,
        update_needs_embed: list[Entity | EntityDeclaration],
    ):
        """Remove deleted relation UUIDs from affected entities and add them to mem_update_skip_embed if needed."""
        entities_to_remove_relations_from = set()
        for relation in state.to_remove:
            entities_to_remove_relations_from.add(relation.lhs if isinstance(relation.lhs, str) else relation.lhs.uuid)
            entities_to_remove_relations_from.add(relation.rhs if isinstance(relation.rhs, str) else relation.rhs.uuid)

        if entities_to_remove_relations_from:
            query_result = await self.db_backend.query(ENTITY_COLLECTION, ids=list(entities_to_remove_relations_from))
            entities_retrieved = [state.lookup_table.get_entity(e) for e in query_result]
            for entity in entities_retrieved:
                if entity.uuid in state.lookup_table.entities:
                    entity = state.lookup_table.entities[entity.uuid]
                update_without_embed = needs_re_embed = False
                for existing_entity_item in update_needs_embed:
                    if isinstance(existing_entity_item, Entity) and existing_entity_item.uuid == entity.uuid:
                        entity = existing_entity_item
                        needs_re_embed = True
                for relation_uuid in state.mem_update.removed_relation.intersection(entity.relations):
                    entity.relations.remove(relation_uuid)
                    if not needs_re_embed:
                        update_without_embed = True
                if (
                    update_without_embed
                    and entity not in state.mem_update_skip_embed.updated_entity
                    and entity.uuid not in state.mem_update.removed_entity
                ):
                    state.mem_update_skip_embed.updated_entity.append(entity)
