# -*- coding: utf-8 -*-
"""BM25 retrieval + index cache. No agent-core dependency, no third-party deps.

Self-implemented BM25Okapi (formula ~40 lines) so the retrieval lib stays
zero-dependency. Parameters k1=0.9, b=0.4 are the short-text values ratel
tuned (``ratel-ai-core`` ``search.rs``) — tool names + descriptions are short
documents, so a small b (low length normalization) fits.

Used as:
  - the degradation fallback when ``embedding_model is None`` (the dense path
    would otherwise ``return []`` and paralyze tool discovery); and
  - one ranker of the ``hybrid`` method (RRF-fused with dense, see ``search.py``).

English ``split()`` is enough here; Chinese descriptions are left to the dense
ranker in ``hybrid`` (each ranker does what it's good at). char n-gram or jieba
can be layered later without touching the call sites.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from .summary import build_tool_summary
from .search import haystack_for

logger = logging.getLogger("jiuwenswarm.common.tool_retrieval.bm25_search")


def tokenize(text: str) -> List[str]:
    """Tokenize for BM25. Lowercase + whitespace split.

    English-oriented (tool names, enum values, field names are ASCII). Chinese
    in descriptions is passed through verbatim; in ``hybrid`` the dense ranker
    covers Chinese semantics, so BM25 doesn't need a Chinese tokenizer here.
    """
    if not text:
        return []
    return [t for t in str(text).lower().split() if t]


class BM25Okapi:
    """BM25Okapi over a fixed corpus (built once, queried many times).

    Stateful index: term doc-freq, per-doc term frequencies, doc lengths, avgdl.
    Pure-text index — no ghost-probe timing — so a sig cache keyed on the name
    set is safe (unlike the executable-corpus filter).
    """

    def __init__(self, corpus_tokens: List[List[str]], k1: float = 0.9, b: float = 0.4):
        self.k1 = k1
        self.b = b
        self.n = len(corpus_tokens)
        self.doc_len = [len(d) for d in corpus_tokens] if corpus_tokens else []
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0

        # term -> doc_freq (# docs containing term)
        self.df: Dict[str, int] = {}
        # doc index -> term -> freq
        self.tf: List[Dict[str, int]] = []
        for doc in corpus_tokens:
            freqs: Dict[str, int] = {}
            for t in doc:
                freqs[t] = freqs.get(t, 0) + 1
            self.tf.append(freqs)
            for term in freqs:  # each unique term in this doc bumps df once
                self.df[term] = self.df.get(term, 0) + 1

        # idf (BM25+ uses log((N - df + 0.5) / (df + 0.5) + 1)); precompute
        self.idf: Dict[str, float] = {}
        for term, df in self.df.items():
            self.idf[term] = math.log(1.0 + (self.n - df + 0.5) / (df + 0.5))

    def get_scores(self, query_tokens: List[str]) -> List[float]:
        """Return a BM25 score per document (same length as corpus)."""
        if self.n == 0:
            return []
        scores = [0.0] * self.n
        for q in query_tokens:
            idf = self.idf.get(q)
            if idf is None:
                continue  # query term not in any doc
            for i in range(self.n):
                f = self.tf[i].get(q, 0)
                if f == 0:
                    continue
                dl = self.doc_len[i] or 0
                denom = f + self.k1 * (1 - self.b + self.b * (dl / self.avgdl if self.avgdl else 0))
                scores[i] += idf * (f * (self.k1 + 1)) / denom
        return scores


class BM25Index:
    """Index binding a BM25Okapi to the tool list it was built from.

    Carries both the ranker and the tool objects so callers can map scores back
    to tools and shape ``build_tool_summary`` results without re-walking the
    tool list.
    """

    def __init__(self, tools: List[Any], desc_cap: int = 256):
        self.tools = list(tools or [])
        self.corpus_tokens = [tokenize(haystack_for(t, desc_cap)) for t in self.tools]
        self.okapi = BM25Okapi(self.corpus_tokens)

    def search(
        self,
        query: str,
        *,
        limit: int,
        detail_level: int,
        min_sim: float = 0.0,
    ) -> List[dict]:
        """Rank tools by BM25 score; return top ``limit`` above ``min_sim``.

        BM25 scores are non-negative; ``min_sim=0`` returns all scored docs
        (useful as a candidate pool for ``hybrid``). Ties broken by tool name
        for determinism (matches ``dense_search``'s tie-break).
        """
        if not self.tools:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores = self.okapi.get_scores(q_tokens)
        scored = [
            (s, self.tools[i]) for i, s in enumerate(scores)
            if s >= min_sim and s > 0.0  # drop 0-score (no term overlap)
        ]
        scored.sort(key=lambda item: (-item[0], str(getattr(item[1], "name", "") or "")))
        matched = [tool for _s, tool in scored[:limit]]
        return [build_tool_summary(tool, detail_level=detail_level) for tool in matched]


def build_bm25_index(tools: List[Any], *, desc_cap: int = 256) -> BM25Index:
    """Build a BM25Index over the given tools."""
    return BM25Index(tools, desc_cap=desc_cap)


def bm25_search(
    query: str,
    tools: List[Any],
    bm25_index: Optional[BM25Index],
    *,
    limit: int,
    detail_level: int,
    desc_cap: int = 256,
    min_sim: float = 0.0,
) -> List[dict]:
    """BM25 retrieval. Builds the index on the fly if ``bm25_index`` is None
    (slow path; rail should pass a cached index). Returns [] on empty/None
    corpus — same empty-contract as ``dense_search``.
    """
    if not tools:
        return []
    if bm25_index is None:
        bm25_index = build_bm25_index(tools, desc_cap=desc_cap)
    return bm25_index.search(query, limit=limit, detail_level=detail_level, min_sim=min_sim)
