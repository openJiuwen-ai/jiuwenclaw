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
import re
from typing import Any, Dict, List, Optional

from .summary import build_tool_summary
from .search import haystack_for

logger = logging.getLogger("jiuwenswarm.common.tool_retrieval.bm25_search")


_CJK_RE = re.compile(r"[一-龥]")


def tokenize(text: str) -> List[str]:
    """Tokenize for BM25. ASCII whitespace-split + CJK unigram/bigram.

    Zero-dependency. ASCII segments (tool names, enum values, field names) are
    whitespace-split as before. CJK runs have no spaces, so ``split()`` leaves
    ``创建定时任务`` as one opaque token that won't match a description
    containing ``定时任务``. Emit unigrams + bigrams for CJK runs so the ranker
    gets overlapping 2-char windows to score on (``创建定时任务`` ->
    ``创建`` / ``定时`` / ``任务`` as bigrams, matching the corpus).

    This is the BM25-side fix for Chinese; dense still covers semantics in
    ``hybrid`` when the model is available, but with this BM25 no longer
    silently misses pure-Chinese queries when dense is absent.
    """
    if not text:
        return []
    s = str(text).lower()
    tokens: List[str] = []
    for seg in re.split(r"([^一-龥]+)", s):
        if not seg:
            continue
        # seg is either a CJK run or a non-CJK run (split captures both sides).
        if _CJK_RE.search(seg):
            for i in range(len(seg)):
                tokens.append(seg[i])
                if i + 1 < len(seg):
                    tokens.append(seg[i:i + 2])
        else:
            tokens.extend(seg.split())
    return tokens


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

        Name-boost (v3 fix, aligns with ``dense_search``): BM25 is purely
        lexical over the haystack, so a tool whose **name** is in the query
        can still lose to a different tool whose description shares more
        Chinese bigrams (term-frequency saturation drowns the exact name
        hit). This is the real failure for MCP tools — their descriptions are
        English while the query is Chinese, so a Chinese-described sibling
        outscores the tool the user named verbatim. Boost name matches so the
        tool the query names actually wins. Boost is scaled to the top BM25
        score (IDF-scale, not cosine), so it stays meaningful at any score
        magnitude. Matches the dense path's tiered boost (exact > contains >
        prefix > token-overlap) so the two rankers stay consistent.
        """
        if not self.tools:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores = self.okapi.get_scores(q_tokens)

        # Name-match boost tier (mirrors dense_search:118-132).
        ql = (query or "").strip().lower()
        ql_norm = "_".join(ql.split())
        top_score = max((s for s in scores if s > 0), default=0.0)

        def _name_boost(tool: Any) -> float:
            if not ql or top_score <= 0:
                return 0.0
            nl = str(getattr(tool, "name", "") or "").lower()
            if not nl:
                return 0.0
            # Scale to the top BM25 score so the boost dominates regardless
            # of IDF magnitude. Exact name in query >> contains > prefix.
            if ql == nl or ql_norm == nl:
                return top_score * 1.0
            if len(nl) >= 5 and (nl in ql_norm or nl in ql):
                return top_score * 0.9
            if len(ql) >= 3 and (nl.startswith(ql) or nl.startswith(ql_norm)):
                return top_score * 0.4
            # Token-overlap: only count LONG, distinctive name tokens (>=5
            # chars). Short tokens like ``list``/``file``/``dir`` are generic
            # and collide across unrelated tools (e.g. ``list_files``'s
            # ``list`` appears inside ``mcp_filesystem_list_allowed_...``,
            # falsely boosting the wrong tool). Require full token equality
            # against a query token, not a substring inside a longer word.
            name_tokens = [t for t in nl.split("_") if len(t) >= 5]
            if name_tokens:
                q_word_tokens = set(ql_norm.split("_")) | set(ql.split())
                hit = sum(1 for t in name_tokens if t in q_word_tokens or t.rstrip("s") in q_word_tokens)
                if hit:
                    ratio = hit / len(name_tokens)
                    return top_score * (0.4 if hit == len(name_tokens) else 0.2 * ratio)
            return 0.0

        scored = []
        for i, s in enumerate(scores):
            if s <= 0.0:
                # A tool with no BM25 term overlap can still be the exact
                # name the query asked for (e.g. Chinese query, English-desc
                # MCP tool). Only name-boost rescues it; without it the tool
                # is invisible to BM25 even when named verbatim.
                b = _name_boost(self.tools[i])
                if b <= 0:
                    continue
                s = b
            else:
                s += _name_boost(self.tools[i])
            if s >= min_sim:
                scored.append((s, self.tools[i]))
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
