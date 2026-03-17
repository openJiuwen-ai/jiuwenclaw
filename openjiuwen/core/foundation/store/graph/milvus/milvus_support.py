# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Milvus Graph Store Schema & Index Definition

Utility function for generating milvus graph store's schema & index
"""

import asyncio
import math
import time
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional

from pymilvus import AnnSearchRequest, MilvusClient, MilvusException

import openjiuwen.core.foundation.store.query as query_expr
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import store_logger
from openjiuwen.core.foundation.store.base_embedding import Embedding
from openjiuwen.core.foundation.store.base_reranker import Reranker
from openjiuwen.core.foundation.store.graph.base_graph_store import GraphStore
from openjiuwen.core.foundation.store.graph.config import GraphConfig
from openjiuwen.core.foundation.store.graph.constants import ENTITY_COLLECTION, EPISODE_COLLECTION, RELATION_COLLECTION
from openjiuwen.core.foundation.store.graph.graph_object import BaseGraphObject, Entity, Episode, Relation
from openjiuwen.core.foundation.store.graph.utils import batched, with_metadata
from openjiuwen.core.foundation.store.query import QueryExpr
from openjiuwen.core.retrieval.common.result_ranking import BaseRankConfig, WeightedRankConfig

from .generate_milvus_schema import generate_schema_and_index


class MilvusGraphStore(GraphStore):
    """
    Reference implementation of GraphBackend, for Milvus Database (lite/standalone/distributed vesions)
    Milvus is an open-source vector database with Apache 2.0 license: https://milvus.io
    """

    def __init__(self, config: GraphConfig):
        self._config = config
        self._embedder = None
        self.client = MilvusClient(
            uri=config.uri,
            token=config.token,
            timeout=config.timeout,
            **config.extras,
        )
        if config.name not in self.client.list_databases(timeout=config.timeout):
            self.client.create_database(config.name, timeout=config.timeout)
        self.client.use_database(config.name, timeout=config.timeout)
        self.metric = config.db_embed_config.distance_metric.replace("dot", "ip").replace("euclidean", "l2").upper()
        self.full_text_search_params = MappingProxyType({"metric_type": "BM25"})
        self.dense_search_params = MappingProxyType({"metric_type": self.metric})
        if config.embedding_model is not None:
            self.attach_embedder(config.embedding_model)
        self.field_def = {
            ENTITY_COLLECTION: [key for key in iter(Entity.model_fields) if not key.endswith("_bm25")],
            RELATION_COLLECTION: [key for key in iter(Relation.model_fields) if not key.endswith("_bm25")],
            EPISODE_COLLECTION: [key for key in iter(Episode.model_fields) if not key.endswith("_bm25")],
        }
        self._build_indices()

    @property
    def config(self) -> GraphConfig:
        """Access graph store config"""
        return self._config

    @property
    def semophore(self) -> Optional[asyncio.Semaphore]:
        """Access graph store semophore"""
        return None if self._embedder is None else self._embedder.limiter

    @property
    def embedder(self) -> Optional[Embedding]:
        """Access graph store embedder"""
        return self._embedder

    @staticmethod
    async def rerank(query: str, candidates: list[Mapping], reranker: Reranker, language: str, **kwargs):
        """Perform cross-encoder re-ranking on retrieval results, sorts candidates in-place.

        Args:
            query (str): Query for retrieval.
            candidates (list[Mapping]): Retrieved candidates, it is sorted in-place.
            reranker (Reranker): Re-ranker to use, a valid reranker instance.
            language (str): Language for prompt to use in re-ranking.
        """
        llm_scores = await reranker.rerank(
            query, [doc.get("content") for doc in candidates], language=language, **kwargs
        )
        for doc in candidates:
            doc["distance"] = llm_scores[doc.get("content")]
        candidates.sort(key=lambda doc: doc.get("distance"), reverse=True)

    @classmethod
    def from_config(cls, config: GraphConfig, **kwargs) -> "MilvusGraphStore":
        """Create a MilvusGraphStore instance from configuration.

        Args:
            config: Graph configuration object
            **kwargs: Additional configuration parameters (ignored)

        Returns:
            Configured MilvusGraphStore instance
        """
        obj = cls(config=config)
        return obj

    def attach_embedder(self, embedder: Embedding):
        if isinstance(embedder, Embedding) and self.embedder is None:
            self._embedder = embedder
        elif self._embedder is not None:
            raise build_error(
                StatusCode.STORE_GRAPH_PARAM_INVALID,
                error_msg=f"Attempt to re-define {type(self).__name__}.embedder.",
            )
        else:
            raise build_error(
                StatusCode.STORE_GRAPH_PARAM_INVALID,
                error_msg=f"Embedder must be instance of Embedding or a subclass of it, got {type(embedder)} instead.",
            )

    def is_empty(self, collection: str) -> bool:
        return not self.client.get_collection_stats(collection).get("row_count")

    def rebuild(self):
        for collection in self.client.list_collections():
            self.client.drop_collection(collection, timeout=self.config.timeout)
        self.client.drop_database(self.config.name, timeout=self.config.timeout)
        if self.config.name not in self.client.list_databases(timeout=self.config.timeout):
            self.client.create_database(self.config.name, timeout=self.config.timeout)
        self.client.use_database(self.config.name, timeout=self.config.timeout)
        self._build_indices()

    def close(self):
        """Close connection to the database"""
        try:
            self.client.close()
        except Exception as e:
            store_logger.error("Failed to close milvus graph store connection: %r", e)

    async def refresh(self, *args, **kwargs):
        return await asyncio.gather(*(self._flush_and_compact(col, *args, **kwargs) for col in self.field_def))

    async def search(
        self,
        query: str,
        k: int,
        collection: str,
        ranker_config: "BaseRankConfig",
        *,
        reranker: Optional[Reranker] = None,
        bfs_depth: int = 0,
        bfs_k: int = 0,
        filter_expr: Optional[QueryExpr] = None,
        output_fields: Optional[list[str]] = None,
        query_embedding: Optional[list[float]] = None,
        **kwargs,
    ) -> dict[str, list[dict]]:
        language: str = kwargs.pop("language", "en")
        min_score: float = kwargs.pop("min_score", 0.0)
        output_dict: dict = kwargs.pop("output_dict", {})

        for col in [ENTITY_COLLECTION, RELATION_COLLECTION, EPISODE_COLLECTION]:
            output_dict[col] = []
        if collection.strip().casefold() == "all":
            tasks: list[asyncio.Task[tuple[dict, str]]] = []
            for col in [ENTITY_COLLECTION, RELATION_COLLECTION, EPISODE_COLLECTION]:
                future = asyncio.create_task(
                    with_metadata(
                        self.search(
                            query=query,
                            k=k,
                            collection=col,
                            ranker_config=ranker_config,
                            bfs_depth=bfs_depth,
                            bfs_k=bfs_k,
                            filter_expr=filter_expr,
                            output_fields=output_fields,
                            min_score=min_score,
                            language=language,
                            reranker=None,
                            query_embedding=query_embedding,
                        ),
                        col,
                    )
                )
                tasks.append(future)
            for future in asyncio.as_completed(tasks):
                result, col = await future
                output_dict[col] = result.get(col)

            # improve entity re-ranking with relations
            await self._combined_rerank(query, output_dict, reranker, language, min_score)

            return output_dict

        output_fields = output_fields or self.field_def.get(collection)
        expr = filter_expr.model_copy() if isinstance(filter_expr, QueryExpr) else None
        if bfs_depth > 0 and collection in (ENTITY_COLLECTION, RELATION_COLLECTION):
            uuids: set[str] = set()
            all_results = dict()
            query_embedding = query_embedding or (await self._query_embedding(query))
            if collection == ENTITY_COLLECTION:
                expansion_fn = self._expand_entities
            else:
                expansion_fn = self._expand_relations
            is_similarity = (self.metric in {"IP", "COSINE"}) if self.config else True
            inf = math.inf if is_similarity else -math.inf
            for graph_expansion in ([True] * bfs_depth) + [False]:
                res = await self._raw_hybrid_search(
                    query,
                    k,
                    collection,
                    ranker_config,
                    skip_ranking=True,
                    query_embedding=query_embedding,
                    expr=expr,
                    output_fields=output_fields,
                    language=language,
                    reranker=None,
                )
                new_results = {doc["uuid"]: doc for doc in res}
                new_uuids = set(new_results.keys()).difference(uuids)
                all_results.update(new_results)

                # graph expansion
                if graph_expansion and new_uuids:
                    new_uuids = (await expansion_fn(filter_expr, new_uuids, lookup=new_results)).difference(uuids)
                    if not new_uuids:
                        break
                    uuids.update(new_uuids)
                    if bfs_k < len(new_uuids):
                        new_uuids = set(
                            sorted(
                                new_uuids,
                                key=lambda x: new_results.get(x, {}).get("distance", inf),
                                reverse=is_similarity,
                            )[:bfs_k]
                        )
                    new_uuids_list = list(new_uuids)
                    if collection == ENTITY_COLLECTION:
                        expr = query_expr.in_list("uuid", new_uuids_list)
                    else:
                        expr = query_expr.in_list("lhs", new_uuids_list) | query_expr.in_list("rhs", new_uuids_list)
                    if filter_expr:
                        expr = filter_expr & expr

            results = (
                await self._rank_results(
                    query,
                    candidates=list(all_results.values()),
                    reranker=reranker,
                    language=language,
                    min_score=min_score,
                )
            )[:k]

            if output_dict is not None:
                output_dict[collection] = results

            return output_dict

        results = await self._raw_hybrid_search(
            query,
            k,
            collection,
            ranker_config,
            skip_ranking=False,
            expr=expr,
            output_fields=output_fields,
            language=language,
            reranker=reranker,
        )

        output_dict[collection] = results

        return output_dict

    async def add_data(self, collection: str, data: Iterable[dict], flush: bool = True, upsert: bool = False, **kwargs):
        insert_func = self.client.upsert if upsert else self.client.insert
        result = insert_func(collection_name=collection, data=list(data), timeout=self.config.timeout, **kwargs)
        if flush:
            self.client.flush(collection_name=collection, timeout=self.config.timeout)
        return result

    async def add_entity(
        self, entities: Iterable[Entity], flush: bool = True, upsert: bool = False, no_embed: bool = False
    ):
        return await self._add_data(ENTITY_COLLECTION, entities, flush, upsert, no_embed)

    async def add_relation(
        self, relations: Iterable[Relation], flush: bool = True, upsert: bool = False, no_embed: bool = False
    ):
        return await self._add_data(RELATION_COLLECTION, relations, flush, upsert, no_embed)

    async def add_episode(
        self, episodes: Iterable[Episode], flush: bool = True, upsert: bool = False, no_embed: bool = False
    ):
        return await self._add_data(EPISODE_COLLECTION, episodes, flush, upsert, no_embed)

    async def query(
        self,
        collection: str,
        ids: Optional[list[Any]] = None,
        expr: Optional[QueryExpr] = None,
        silence_errors: bool = False,
        **kwargs,
    ) -> list[dict]:
        """Execute query on database

        Args:
            collection (str): Collection to query on.
            ids (Optional[list[Any]], optional): list of uuids to fetch directly. Defaults to None.
            expr (Optional[QueryExpr], optional): Filtering expression, ignored if ids is not None.\
                Defaults to None.
            silence_errors (bool): Supresses MilvusExceptions and return empty list instead. Defaults to False.
            **kwargs: Additional arguments to pass into query, such as "limit".

        Raises error:
            - if "expr" and "ids" are both None, an integer "limit" argument must be supplied.

        Returns:
            list[dict]: Query result.
        """
        if expr:
            expr_str = expr.to_expr("milvus")
        elif "limit" not in kwargs and ids is None:
            raise build_error(
                StatusCode.STORE_GRAPH_PARAM_INVALID,
                error_msg='Argument "limit" must be set to positive integer when "expr" and "ids" are None',
            )
        else:
            expr_str = None
        output_fields = kwargs.pop("output_fields", self.field_def[collection])
        if ids:
            query_method = self.client.get
            query_args = dict(
                collection_name=collection, ids=ids, output_fields=output_fields, timeout=self.config.timeout, **kwargs
            )
        else:
            query_method = self.client.query
            query_args = dict(
                collection_name=collection,
                filter=expr_str,
                output_fields=output_fields,
                timeout=self.config.timeout,
                **kwargs,
            )
        if silence_errors:
            try:
                return query_method(**query_args)
            except MilvusException:
                return []
        return query_method(**query_args)

    async def delete(
        self, collection: str, ids: Optional[list[Any]] = None, expr: Optional[QueryExpr] = None, **kwargs
    ) -> dict:
        """Delete records from database

        Args:
            collection (str): Collection to perform deletion on: "entity", "relation", "episode".
            ids (Optional[list[Any]], optional): list of uuids for records to delete. Defaults to None.
            expr (Optional[QueryExpr], optional): Filtering expression, ignored if ids is not None.\
                Defaults to None.
            **kwargs: Additional arguments to pass into query.

        Raises error:
            - "expr" and "ids" are both None.

        Returns:
            dict: Deletion result.
        """
        if ids:
            expr_str = query_expr.in_list("uuid", ids).to_expr("milvus")
        elif expr:
            expr_str = expr.to_expr("milvus")
        else:
            raise build_error(
                StatusCode.STORE_GRAPH_PARAM_INVALID,
                error_msg='Either "ids" or "expr" must be supplied',
            )
        return self.client.delete(collection, timeout=self.config.timeout, filter=expr_str, **kwargs)

    def _build_indices(self):
        """Build indices & collections for database"""
        self.embed_dim = self.config.embed_dim

        for col in [ENTITY_COLLECTION, RELATION_COLLECTION, EPISODE_COLLECTION]:
            if self.client.has_collection(col, timeout=self.config.timeout):
                try:
                    self.client.load_collection(col)
                    continue
                except MilvusException as e:
                    db_name = self.config.name
                    store_logger.error("Milvus graph store failed to load collection (%s/%s): %s", db_name, col, e)
                    self.rebuild()
                    return

            schema, index_params = generate_schema_and_index(
                self.client,
                collection=col,
                storage_config=self.config.db_storage_config,
                embed_config=self.config.db_embed_config,
                dim=self.embedder.dimension,
            )

            self.client.create_collection(
                collection_name=col,
                primary_field_name="uuid",
                id_type="varchar",
                schema=schema,
                index_params=index_params,
                vector_field_name="content_embedding",
                dimension=self.embed_dim,
                metric_type=self.metric,
                auto_id=False,
            )

            self.client.load_collection(col)

    async def _rank_results(
        self,
        query: str,
        candidates: list[dict],
        reranker: Optional[Reranker],
        language: str,
        min_score: float = 0.0,
    ) -> list[dict]:
        """Internal helper function for ranking retrieval results (and re-ranking if reranker is supplied)"""
        is_similarity = self.metric in {"IP", "COSINE"}
        if is_similarity:
            candidates = [doc for doc in candidates if doc["distance"] >= min_score]
        else:
            candidates = [doc for doc in candidates if doc["distance"] <= min_score]
        if reranker:
            # Cross-encoder re-ranking
            await self.rerank(query, candidates=candidates, reranker=reranker, language=language)
        else:
            # If is similarity not distance, higher is better, otherwise lower is better
            is_similarity = self.metric in {"IP", "COSINE"}
            candidates.sort(key=lambda doc: doc["distance"], reverse=is_similarity)
        return candidates

    async def _combined_rerank(
        self, query: str, results: dict, reranker: Optional[Reranker], language: str, min_score: float = 0.0
    ):
        """Internal helper function for combined re-ranking (using relations to aid entity re-ranking)"""
        if reranker is None:
            return

        entities = [ent.copy() for ent in results[ENTITY_COLLECTION]]
        relations = [rel.copy() for rel in results[RELATION_COLLECTION]]
        rel_uuids = {rel["uuid"]: rel for rel in relations}
        for ent in entities:
            ent["original_content"] = ent.get("content", "")
            content = []
            for rel_id in ent.get("relations", []):
                if rel_id in rel_uuids:
                    rel = rel_uuids[rel_id]
                    content.append((rel["content"], rel["distance"]))
            content.sort(key=lambda rel: rel[1], reverse=True)
            content = [(ent["original_content"], -1), ("-" * 10, -1)] + content
            mentions = len(content) - 2
            if mentions > 0:
                ent["content"] = "\n - ".join(line for line, _ in content)

        entities = await self._rank_results(
            query, candidates=entities, reranker=reranker, language=language, min_score=min_score
        )

        for ent in entities:
            ent["content"] = ent["original_content"]
            del ent["original_content"]

        results[ENTITY_COLLECTION] = entities

    async def _expand_entities(self, expr: QueryExpr, uuids: set[str], **kwargs) -> set:
        """Graph expansion method for entity retrieval (expand by relations)"""
        if uuids:
            final_expr = query_expr.in_list("lhs", uuids) | query_expr.in_list("rhs", uuids)
            if expr:
                final_expr = expr & final_expr
            results = self.client.query(
                RELATION_COLLECTION, filter=final_expr.to_expr("milvus"), output_fields=["lhs", "rhs"]
            )
            expansion_results = {doc["lhs"] for doc in results}
            expansion_results.update(doc["rhs"] for doc in results)
            return expansion_results
        return set()

    async def _expand_relations(self, expr: QueryExpr, uuids: set[str], lookup: dict[str, dict], **kwargs) -> set:
        """Graph expansion method for relation retrieval (expand by entities)"""
        if uuids:
            node_uuids = []
            for relation_uuid in uuids:
                relation = lookup.get(relation_uuid)
                node_uuids.append(relation["lhs"])
                node_uuids.append(relation["rhs"])

            final_expr = query_expr.in_list("uuid", set(node_uuids))
            if expr:
                final_expr = expr & final_expr
            results = self.client.query(
                ENTITY_COLLECTION,
                filter=final_expr.to_expr("milvus"),
                output_fields=["relations"],
            )

            expansion_results = []
            for entity in results:
                expansion_results.extend(entity["relations"])
            return set(expansion_results)
        return set()

    async def _query_embedding(self, query: str) -> list[float]:
        return await self.embedder.embed_query(query)

    async def _add_data(
        self,
        collection: str,
        data: Iterable[BaseGraphObject],
        flush: bool = True,
        upsert: bool = False,
        no_embed: bool = False,
    ):
        """Internal helper function for adding data to database"""
        t_start = time.time()
        data = list(data)
        if data:
            # Fetch all embedding tasks in the form of (obj, attribute, value) tuples
            embed_task_metadata = []
            if not no_embed:
                for graph_object in data:
                    embed_task_metadata.extend(graph_object.fetch_embed_task())

            # Perform embedding tasks and set respective attributes
            embed_result = await self.embedder.embed_documents(
                [task_tuple[-1] for task_tuple in embed_task_metadata], batch_size=self.config.embed_batch_size
            )
            for (obj, attribute, _), embedding in zip(embed_task_metadata, embed_result):
                setattr(obj, attribute, embedding)

            data_processed: list[dict] = []
            for item in data:
                if len(item.content) > self.config.db_storage_config.content:
                    item.content = item.content[: self.config.db_storage_config.content - 3] + "..."
                item_name = getattr(item, "name", "")
                if len(item_name) > self.config.db_storage_config.name:
                    setattr(item, "name", item_name[: self.config.db_storage_config.name - 3] + "...")
                entity_dict = {k: v for k, v in item.model_dump().items() if v is not None}
                data_processed.append(entity_dict)
            insert_func = self.client.upsert if upsert else self.client.insert
            try:
                insert_func(collection, data=data_processed, timeout=self.config.timeout)
            except MilvusException:
                # Maybe too much data
                store_logger.info(
                    "Milvus data addition failed, try batching with size of %d", self.config.embed_batch_size
                )
                try:
                    self.client.delete(collection, ids=[x.uuid for x in data])
                except Exception as e:
                    store_logger.warning("Milvus data addition failure clean up failed: %r", e)
                for batch in batched(data_processed, self.config.embed_batch_size):
                    insert_func(collection, data=list(batch), timeout=self.config.timeout)
        if flush:
            self.client.flush(collection, timeout=self.config.timeout)
        store_logger.debug("Add graph memory [%s] took %gs", collection, time.time() - t_start)

    async def _raw_hybrid_search(
        self,
        query: str,
        k: int,
        collection: str,
        ranker_config: "BaseRankConfig",
        *,
        skip_ranking: bool = False,
        query_embedding: Optional[list[float]] = None,
        expr: str | QueryExpr = "",
        output_fields: Iterable[str] = ("id", "uuid", "content"),
        **kwargs,
    ) -> list[dict]:
        """Internal method for performing hybrid search on a collection"""
        language: str = kwargs.pop("language", "en")
        reranker = kwargs.pop("reranker", None)
        min_score: float = kwargs.pop("min_score", 0.0)
        query_embedding = query_embedding or (await self._query_embedding(query))

        if isinstance(expr, QueryExpr):
            expr = expr.to_expr("milvus")
        if not isinstance(expr, str):
            expr = ""

        search_requests = self._get_search_req(query=query, query_embedding=query_embedding, k=k, expr=expr)

        ranker, search_requests = self._get_ranker_and_reqs(
            ranker_config=ranker_config, collection=collection, search_requests=search_requests
        )

        # Execute hybrid search request
        result = self.client.hybrid_search(
            collection,
            search_requests,
            ranker=ranker,
            limit=k,
            output_fields=list(output_fields),
        )[0]
        result = [dict(distance=r.get("distance")) | r.get("entity", {}) for r in result]

        if skip_ranking:
            return result

        return await self._rank_results(
            query, candidates=result, reranker=reranker, language=language, min_score=min_score
        )

    async def _flush_and_compact(self, collection: str, *args, **kwargs):
        self.client.flush(collection, *args, **kwargs)
        if kwargs.get("skip_compact"):
            return None
        return self.client.compact(collection, *args, **kwargs)

    def _get_ranker_and_reqs(self, ranker_config: BaseRankConfig, collection: str, search_requests: list):
        ranker_config = ranker_config.model_copy()
        if isinstance(ranker_config, WeightedRankConfig):
            if collection == EPISODE_COLLECTION:
                ranker_config.dense_name = 0
            elif collection == RELATION_COLLECTION:
                ranker_config.dense_name = 0
            weights = [
                ranker_config.dense_name,
                ranker_config.dense_content,
                ranker_config.sparse_content,
            ]
        else:
            weights = [float(w) for w in ranker_config.is_active]
            if collection == EPISODE_COLLECTION:
                weights[0] = weights[1] = 0
            elif collection == RELATION_COLLECTION:
                weights[0] = 0

        # Adjust search based on ranking config
        search_requests = [req for req, weight in zip(search_requests, weights) if weight > 0]
        ranker_cls = ranker_config.get_ranker_cls("milvus")
        ranker_args, ranker_kwargs = ranker_config.args
        return ranker_cls(*ranker_args, **ranker_kwargs), search_requests

    def _get_search_req(self, query: str, query_embedding: list[float], k: int, expr: str) -> list[AnnSearchRequest]:
        """Prepare sparse and dense vector search requests"""
        dense_req_name = AnnSearchRequest(
            [query_embedding],
            "name_embedding",
            self.dense_search_params,
            limit=k * 2,
            expr=expr,
        )
        dense_req_content = AnnSearchRequest(
            [query_embedding],
            "content_embedding",
            self.dense_search_params,
            limit=k * 2,
            expr=expr,
        )
        full_text_search_params = self.full_text_search_params.copy()
        sparse_req_content = AnnSearchRequest(
            [query],
            "content_bm25",
            full_text_search_params,
            limit=k * 2,
            expr=expr,
        )
        return [dense_req_name, dense_req_content, sparse_req_content]
