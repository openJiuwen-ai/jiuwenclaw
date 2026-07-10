from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Any

from . import ExperienceBank
from .bank import ExperienceBank
from .cluster import ClusteredQuery, cluster_traces, _faiss_cluster, populate_cluster
from .distiller import TraceDistiller
from .embed import EmbeddingClient
from .models import ExperienceBankBuildConfig, ExperienceItem, TraceRecord

LOGGER = logging.getLogger(__name__)


class ExperienceBaseBuilder:
    """Build an ``ExperienceBank`` index from parsed traces via
    cluster → distill → persist pipeline.
    """

    def __init__(
        self,
        kb: ExperienceBank,
        embedding_client: EmbeddingClient,
        llm_client: Any,
        llm_model: str,
        *,
        skills_info: list[dict[str, str]] | None = None,
        build_config: ExperienceBankBuildConfig | None = None,
    ) -> None:
        self._kb = kb
        self._embedder = embedding_client
        if not llm_client:
            raise ValueError("ExperienceBaseBuilder requires llm_client for distillation.")
        self._llm = llm_client
        if not llm_model:
            raise ValueError("ExperienceBaseBuilder requires llm_model for distillation.")
        self._llm_model = llm_model
        self._skills_info = skills_info
        self._config = build_config or ExperienceBankBuildConfig()
        self._min_cluster_size = self._config.min_cluster_size
        self._max_workers = self._config.max_workers
        self._max_success_examples = self._config.max_success_examples
        self._pending: list[TraceRecord] = []
        self._flush_threshold = self._config.pending_flush_threshold
        self._min_hits = self._config.min_hits_for_pattern
        self._lock = threading.Lock()

    def build(self, traces: list[TraceRecord]) -> int:
        """Build the experience KB from a list of parsed ``TraceRecord``.

        Pipeline stages:
            1. **Cluster** — group by skill set, then semantic cluster via FAISS
            2. **Distill** — LLM distills each cluster into a generalized pattern
            3. **Write** — write distilled patterns into ``ExperienceBank``

        Returns the number of experience items created.

        Raises:
            ValueError: if the target KB already contains entries (full
            rebuild only — use a fresh directory to avoid accidental data loss).
        """
        if self._kb.count > 0:
            LOGGER.error(
                "ExperienceBaseBuilder: refusing to build — "
                "target KB directory is not empty (existing %d entries). "
                "This is a full-build operation; use a fresh directory to avoid overwriting data.",
                self._kb.count,
            )
            raise ValueError(
                f"KB directory is not empty: {self._kb.count} entries exist. "
                f"ExperienceBaseBuilder performs a full build and will overwrite existing data. "
                f"Use a fresh directory or clear the KB first."
            )

        t0 = time.monotonic()

        if not traces:
            LOGGER.warning("TraceIndexBuilder: no traces provided, skipping")
            return 0

        # --- Cluster ---
        t1 = time.monotonic()
        clusters = cluster_traces(traces, self._embedder, self._min_cluster_size)
        cluster_elapsed = time.monotonic() - t1
        LOGGER.info(
            "TraceIndexBuilder: clustering done: %d clusters in %.2fs",
            len(clusters), cluster_elapsed,
        )

        if not clusters:
            LOGGER.warning("TraceIndexBuilder: no clusters formed, skipping")
            return 0

        # --- Distill ---
        t2 = time.monotonic()
        distiller = TraceDistiller(
            self._llm,
            self._llm_model,
            skills_info=self._skills_info,
            max_workers=self._max_workers,
            max_success_examples=self._max_success_examples,
        )
        distilled = distiller.run(clusters)
        distill_elapsed = time.monotonic() - t2
        LOGGER.info(
            "TraceIndexBuilder: distillation done: %d patterns in %.2fs",
            len(distilled), distill_elapsed,
        )

        # --- Write to KB ---
        t3 = time.monotonic()
        cluster_by_id = {c.cluster_id: c for c in clusters}
        batch_items = []

        for pattern in distilled:
            if not pattern.pattern_description:
                continue

            top_skills = pattern.effective_skills[0] if pattern.effective_skills else []
            cluster = cluster_by_id.get(pattern.cluster_id)
            examples = [trace.query for trace in cluster.success_traces] if cluster else [pattern.pattern_description]
            item = self._kb.create_item(
                query_pattern=pattern.pattern_description,
                query_examples=examples[:5],
                skill_ids=top_skills,
                success_count=pattern.raw_trace_count,
            )
            batch_items.append(item)

        self._kb.add_batch(batch_items)
        created = len(batch_items)
        build_index_elapsed = time.monotonic() - t3
        total_elapsed = time.monotonic() - t0
        LOGGER.info(
            "TraceIndexBuilder: build index done: %d entries in %.2fs",
            created, build_index_elapsed,
        )
        LOGGER.info(
            "TraceIndexBuilder: pipeline total: %.2fs, created %d entries",
            total_elapsed, created,
        )
        return created


    def add(self, trace: TraceRecord) -> None:
        """Record a successful query-skill mapping.

        This adds to the pending buffer. Call flush() to cluster and persist.
        """
        with self._lock:
            self._pending.append(trace)
            pending_count = len(self._pending)

        # Auto-flush if buffer is large enough (non-blocking)
        if pending_count >= self._flush_threshold:
            with self._lock:
                snapshot = list(self._pending)
                self._pending.clear()
            # Flush outside the lock to avoid blocking add() during LLM calls
            self._flush_snapshot(snapshot)

    def flush(self) -> int:
        """Cluster pending records and merge into the KB.

        Returns the number of new experience items created.
        Blocks until complete — use for graceful shutdown only.
        """
        with self._lock:
            if not self._pending:
                return 0
            pending = list(self._pending)
            self._pending.clear()

        return self._flush_snapshot(pending, force=True)

    def _flush_snapshot(self, pending: list[TraceRecord], force: bool = False) -> int:
        """Flush a snapshot of pending records. Safe to call from any thread."""
        if not pending:
            return 0

        # Step 1: group by skill_ids to reduce noise first
        by_skill = defaultdict(list)
        for r in pending:
            by_skill[tuple(sorted(r.skills))].append(r)

        created = 0
        for skill_key, records in by_skill.items():
            created += self._cluster_and_merge(records, list(skill_key), force=force)

        LOGGER.info(
            "ExperienceBaseBuilder: flushed %d pending records, created %d experience items",
            len(pending), created,
        )
        return created

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _cluster_and_merge(
        self,
        records: list[TraceRecord],
        skill_ids: list[str],
        force: bool = False,
    ) -> int:
        """Embed records, cluster by semantic similarity, name each cluster,
        and write to KB.

        Returns number of items created.
        """
        if len(records) < self._min_hits and not force:
            # Too few records — put back into pending for later
            self._pending.extend(records)
            return 0

        # Cluster by embedding
        queries = [r.query for r in records]
        embeddings = self._embedder.embed_batch(queries)

        cluster_labels = _faiss_cluster(embeddings, min_cluster_size=1)

        created = 0
        from collections import defaultdict as _dd
        clusters: dict[int, list[TraceRecord]] = _dd(list)
        noise: list[TraceRecord] = []
        local_clusters: dict[int, list[int]] = {}
        for i, label in enumerate(cluster_labels):
            if label >= 0:
                clusters[label].append(records[i])
                local_clusters.setdefault(label, []).append(i)
            else:
                noise.append(records[i])

        # Put noise back into pending (only if not forcing)
        if not force:
            self._pending.extend(noise)

        # Name each cluster and write to KB
        cluster_id_offset = 0
        for label, cluster_records in clusters.items():
            if len(cluster_records) < self._min_hits and not force:
                self._pending.extend(cluster_records)
                continue
            cid = self._kb.count + cluster_id_offset
            cluster_query = populate_cluster(cid, embeddings, local_clusters[label], cluster_records)
            item = self._try_merge_into_existing(cluster_query, skill_ids)
            if item:
                created += 1
                cluster_id_offset += 1
        return created

    def _try_merge_into_existing(
        self,
        cluster: ClusteredQuery,
        skill_ids: list[str],
    ) -> ExperienceItem | None:
        """Check if an experience with similar pattern and same skills already exists.
        If yes, skip (deduplication). If no, create a new item.
        """
        distiller = TraceDistiller(
            self._llm,
            self._llm_model,
            skills_info=self._skills_info,
            max_workers=self._max_workers,
            max_success_examples=self._max_success_examples,
        )
        distilled = distiller.run([cluster])
        if not distilled:
            return None
        examples = [trace.query for trace in cluster.member_traces]

        # 先做 embedding 去重
        items = self._kb.search_by_embedding(distilled[0].pattern_description, threshold=0.75)
        if items:
            # 检查是否有相同 skill 组合的 item
            for _, existing in items:
                if set(existing.skill_ids) == set(skill_ids):
                    return None  # 完全重复，跳过

        return self._create_new_item(distilled[0].pattern_description, examples, skill_ids)



    def _create_new_item(
        self,
        pattern: str,
        query_examples: list[str],
        skill_ids: list[str],
    ) -> ExperienceItem:
        """Helper to create a new experience item."""
        item = self._kb.create_item(
            query_pattern=pattern,
            query_examples=query_examples[:5],
            skill_ids=skill_ids,
        )
        self._kb.add(item)
        return item

__all__ = ["ExperienceBaseBuilder"]
