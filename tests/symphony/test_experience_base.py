"""Tests for ``ExperienceBaseBuilder._merge_similar_patterns``.

Covers the pattern-deduplication behavior introduced to collapse
near-duplicate distilled patterns (embedding cosine similarity >=
``pattern_merge_threshold``) into a single experience item, sampling
``merged_query_examples_count`` queries from the pooled success traces.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from jiuwenswarm.symphony.experience.cluster import ClusteredQuery, cluster_traces
from jiuwenswarm.symphony.experience.collector import ExperienceBaseBuilder
from jiuwenswarm.symphony.experience.retriever import ExperienceRetriever
from jiuwenswarm.symphony.experience.models import (
    DistilledPattern,
    ExperienceBankBuildConfig,
    ExperienceItem,
    TraceRecord,
)

class FakeEmbedder:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = {k: list(v) for k, v in vectors.items()}
        self.embed_calls: list[str] = []

    def _unit(self, vec: list[float]) -> list[float]:
        arr = np.asarray(vec, dtype=np.float32)
        n = float(np.linalg.norm(arr))
        if n == 0.0:
            return list(arr)
        return (arr / n).tolist()

    def embed(self, text: str) -> list[float]:
        self.embed_calls.append(text)
        return self._unit(self._vectors[text])

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class FakeBank:
    """Minimal stand-in for ``ExperienceBank`` recording created items."""

    def __init__(self) -> None:
        self.items: list[ExperienceItem] = []
        self._counter = 0

    @property
    def count(self) -> int:
        return len(self.items)

    def create_item(
        self,
        query_pattern: str,
        query_examples: list[str],
        skill_ids: list[str],
        success_count: int = 1,
    ) -> ExperienceItem:
        self._counter += 1
        return ExperienceItem(
            id=f"exp_{self._counter:04d}",
            query_pattern=query_pattern,
            query_examples=list(query_examples),
            skill_ids=list(skill_ids),
            success_count=success_count,
            created_at=0.0,
            last_hit_at=0.0,
        )

    def add_batch(self, items: list[ExperienceItem]) -> None:
        self.items.extend(items)


class _FakeBankForRetriever:
    """ExperienceBank stand-in exposing ``search_by_embedding`` for the
    retriever. Returns a fixed, ordered list of (score, item) so the test can
    assert the dedup + order-preservation behavior of ``ExperienceRetriever``.
    """

    def __init__(self, results: list[tuple[float, ExperienceItem]]) -> None:
        self._results = results
        self.search_calls: list[str] = []

    def search_by_embedding(
            self,
            query: str,
            top_k: int = 1,
            threshold: float = 0.80,
    ) -> list[tuple[float, ExperienceItem]]:
        self.search_calls.append(query)
        return list(self._results)


def _item(item_id: str, skill_ids: list[str]) -> ExperienceItem:
    return ExperienceItem(
        id=item_id,
        query_pattern="p",
        query_examples=[],
        skill_ids=list(skill_ids),
        success_count=1,
        created_at=0.0,
        last_hit_at=0.0,
    )


def _pattern(
    cluster_id: int,
    description: str,
    *,
    skills: list[str] | None = None,
) -> DistilledPattern:
    return DistilledPattern(
        cluster_id=cluster_id,
        effective_skills=list(skills) if skills else [],
        pattern_description=description,
    )


def _cluster(cluster_id: int, success_queries: list[str]) -> ClusteredQuery:
    traces = [
        TraceRecord(
            trace_id=f"t{cluster_id}_{i}",
            query=q,
            skills=[],
            messages=[],
            result="",
            success=True,
        )
        for i, q in enumerate(success_queries)
    ]
    return ClusteredQuery(
        cluster_id=cluster_id,
        centroid_query=success_queries[0] if success_queries else "",
        member_traces=list(traces),
        success_traces=list(traces),
        failure_traces=[],
    )


def _builder(
    embedder: FakeEmbedder,
    *,
    merge_threshold: float = 0.9,
    sample_count: int = 5,
    kb: Any = None,
) -> ExperienceBaseBuilder:
    # Bypass __init__ — we only exercise the merge / create paths.
    import random
    b = ExperienceBaseBuilder.__new__(ExperienceBaseBuilder)
    b._embedder = embedder
    b._pattern_merge_threshold = merge_threshold
    b._query_examples_count = sample_count
    b._kb = kb
    b._rng = random.Random(42)
    return b

class _RecordingDistiller:
    """Distiller stub returning exactly the patterns handed to it, so we can
    isolate the merge + write stages of ``build`` without an LLM call."""

    def __init__(self, patterns: list[DistilledPattern]) -> None:
        self._patterns = patterns

    def run(self, _clusters: list[Any]) -> list[DistilledPattern]:
        return list(self._patterns)


def test_build_drops_traces_with_success_false(monkeypatch) -> None:
    """build() filters out success=False traces before they reach cluster_traces."""
    embedder = FakeEmbedder({"p": [1.0, 0.0]})
    bank = FakeBank()

    captured: list[list[TraceRecord]] = []

    def fake_cluster_traces(traces, *_a, **_k):
        captured.append(list(traces))
        return [ClusteredQuery(0, "ok", [], [], [])]

    import jiuwenswarm.symphony.experience.collector as collector_mod
    monkeypatch.setattr(collector_mod, "cluster_traces", fake_cluster_traces)
    monkeypatch.setattr(
        collector_mod, "TraceDistiller",
        lambda *a, **kw: _RecordingDistiller([_pattern(0, "p", skills=["s"])]),
    )

    builder = ExperienceBaseBuilder(
        kb=bank,
        embedding_client=embedder,
        llm_client=object(),
        llm_model="any",
        build_config=ExperienceBankBuildConfig(),
    )

    traces = [
        TraceRecord(trace_id="ok1", query="q1", skills=["s"], success=True),
        TraceRecord(trace_id="bad1", query="q2", skills=["s"], success=False),
        TraceRecord(trace_id="bad2", query="q3", skills=["s"], success=False),
    ]
    builder.build(traces)

    assert len(captured) == 1
    assert [t.trace_id for t in captured[0]] == ["ok1"]


def test_build_drops_traces_with_empty_skills(monkeypatch) -> None:
    """build() filters out traces with empty skills list before cluster_traces."""
    embedder = FakeEmbedder({"p": [1.0, 0.0]})
    bank = FakeBank()

    captured: list[list[TraceRecord]] = []

    def fake_cluster_traces(traces, *_a, **_k):
        captured.append(list(traces))
        return [ClusteredQuery(0, "ok", [], [], [])]

    import jiuwenswarm.symphony.experience.collector as collector_mod
    monkeypatch.setattr(collector_mod, "cluster_traces", fake_cluster_traces)
    monkeypatch.setattr(
        collector_mod, "TraceDistiller",
        lambda *a, **kw: _RecordingDistiller([_pattern(0, "p", skills=["s"])]),
    )

    builder = ExperienceBaseBuilder(
        kb=bank,
        embedding_client=embedder,
        llm_client=object(),
        llm_model="any",
        build_config=ExperienceBankBuildConfig(),
    )

    traces = [
        TraceRecord(trace_id="ok1", query="q1", skills=["s"], success=True),
        TraceRecord(trace_id="bad1", query="q2", skills=[], success=True),
        TraceRecord(trace_id="bad2", query="q3", skills=[], success=True),
    ]
    builder.build(traces)

    assert len(captured) == 1
    assert [t.trace_id for t in captured[0]] == ["ok1"]


def test_build_returns_zero_when_all_traces_invalid(monkeypatch) -> None:
    """All traces invalid -> build() returns 0 without calling cluster_traces."""
    embedder = FakeEmbedder({})
    bank = FakeBank()

    called = {"cluster": False}

    def fake_cluster_traces(*_a, **_k):
        called["cluster"] = True
        return []

    import jiuwenswarm.symphony.experience.collector as collector_mod
    monkeypatch.setattr(collector_mod, "cluster_traces", fake_cluster_traces)

    builder = ExperienceBaseBuilder(
        kb=bank,
        embedding_client=embedder,
        llm_client=object(),
        llm_model="any",
        build_config=ExperienceBankBuildConfig(),
    )

    traces = [
        TraceRecord(trace_id="bad1", query="q1", skills=["s"], success=False),
        TraceRecord(trace_id="bad2", query="q2", skills=[], success=True),
    ]
    created = builder.build(traces)

    assert created == 0
    assert called["cluster"] is False


def test_build_logs_dropped_count(monkeypatch) -> None:
    """build() logs how many invalid traces were dropped."""
    import logging
    embedder = FakeEmbedder({"p": [1.0, 0.0]})
    bank = FakeBank()

    captured: list[list[TraceRecord]] = []

    def fake_cluster_traces(traces, *_a, **_k):
        captured.append(list(traces))
        return [ClusteredQuery(0, "ok", [], [], [])]

    import jiuwenswarm.symphony.experience.collector as collector_mod
    monkeypatch.setattr(collector_mod, "cluster_traces", fake_cluster_traces)
    monkeypatch.setattr(
        collector_mod, "TraceDistiller",
        lambda *a, **kw: _RecordingDistiller([_pattern(0, "p", skills=["s"])]),
    )

    builder = ExperienceBaseBuilder(
        kb=bank,
        embedding_client=embedder,
        llm_client=object(),
        llm_model="any",
        build_config=ExperienceBankBuildConfig(),
    )

    traces = [
        TraceRecord(trace_id="ok1", query="q1", skills=["s"], success=True),
        TraceRecord(trace_id="bad1", query="q2", skills=["s"], success=False),
        TraceRecord(trace_id="bad2", query="q3", skills=[], success=True),
    ]

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append
    logger = logging.getLogger("jiuwenswarm.symphony.experience.collector")
    prev_level = logger.level
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        count = builder.build(traces)
        assert count == 1
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)

    msgs = [r.getMessage() for r in records]
    assert any("dropped 2" in m and "3 provided" in m for m in msgs)


def _add_builder(*, flush_threshold: int = 100) -> ExperienceBaseBuilder:
    """Minimal builder for add()-only tests: just the fields add() touches."""
    import threading
    b = ExperienceBaseBuilder.__new__(ExperienceBaseBuilder)
    b._pending = []
    b._flush_threshold = flush_threshold
    b._lock = threading.Lock()
    return b



def test_add_accepts_valid_trace() -> None:
    builder = _add_builder()

    trace = TraceRecord(trace_id="t1", query="q", skills=["s"], success=True)
    builder.add(trace)
    assert len(builder._pending) == 1
    assert builder._pending[0].trace_id == "t1"

    bad_trace = TraceRecord(trace_id="bad", query="q", skills=[], success=True)
    builder.add(bad_trace)
    assert len(builder._pending) == 1


def test_retriever_dedups_shared_skills_preserving_similarity_order() -> None:
    # Two items, both referencing skill "s1"; the higher-score item also
    # carries "s2". Without dedup the flat comprehension would yield
    # ["s1", "s2", "s1"].
    results = [
        (0.95, _item("exp_0001", ["s1", "s2"])),
        (0.88, _item("exp_0002", ["s1"])),
    ]
    kb = _FakeBankForRetriever(results)
    retriever = ExperienceRetriever(kb=kb, threshold=0.5, top_k=2)

    out = retriever.search("q")

    assert out == ["s1", "s2"]
    # First occurrence wins: s1 comes from the higher-score item (exp_0001),
    # not the lower one (exp_0002), and is not repeated.
    assert out[0] == "s1"
    assert out.count("s1") == 1


def test_retriever_returns_empty_when_no_results() -> None:
    kb = _FakeBankForRetriever([])
    retriever = ExperienceRetriever(kb=kb, threshold=0.5, top_k=2)

    assert retriever.search("q") == []


def test_retriever_keeps_disjoint_skills_in_order() -> None:
    # No overlap between items — order must still follow FAISS similarity rank.
    results = [
        (0.9, _item("exp_0001", ["alpha"])),
        (0.8, _item("exp_0002", ["beta"])),
        (0.7, _item("exp_0003", ["gamma"])),
    ]
    kb = _FakeBankForRetriever(results)
    retriever = ExperienceRetriever(kb=kb, threshold=0.5, top_k=3)

    assert retriever.search("q") == ["alpha", "beta", "gamma"]


def test_retriever_dedups_repeated_skills_within_a_single_item() -> None:
    """The dedup runs on the flattened generator, so duplicates *within* a
    single item's ``skill_ids`` list are also collapsed — not just repeats
    across items. First-occurrence order is preserved.

    Against the pre-dedup comprehension the input below would yield
    ``["s1", "s2", "s1", "s3", "s2"]``; the fix produces ``["s1", "s2", "s3"]``.
    """
    results = [
        (0.95, _item("exp_0001", ["s1", "s2", "s1", "s3", "s2"])),
    ]
    kb = _FakeBankForRetriever(results)
    retriever = ExperienceRetriever(kb=kb, threshold=0.5, top_k=1)

    out = retriever.search("q")

    assert out == ["s1", "s2", "s3"]
    for skill in ("s1", "s2", "s3"):
        assert out.count(skill) == 1