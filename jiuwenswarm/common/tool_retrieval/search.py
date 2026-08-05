# -*- coding: utf-8 -*-
"""Dense retrieval + boost logic. No agent-core dependency, fully self-contained.

Operates on duck-typed tool objects (``getattr(name/description/parameters)``).
Haystack building and output-dict shaping use the locally-ported ``summary``
helpers (verbatim from agent-core's base class) — no injected callables, so the
module runs standalone (can be unit-tested / reused outside agent-core).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from .summary import build_tool_summary, parameters_to_text

logger = logging.getLogger("jiuwenswarm.common.tool_retrieval.search")

VERB_INTENT = (
    (("create", "add"), ("创建", "新增", "新建", "添加", "建立", "生成", "create", "add")),
    (("list",), ("列出", "列表", "list")),
    (("delete", "remove"), ("删除", "移除", "remove", "delete")),
    (("update",), ("修改", "更新", "update")),
    (("preview",), ("预览", "preview")),
    (("get",), ("获取", "get")),
    (("toggle",), ("切换", "开关", "toggle")),
    (("run",), ("运行", "执行", "run", "runs")),
    (("send", "deliver"), ("发送", "投递", "传送", "send", "deliver")),
    (("search",), ("搜索", "查找", "检索", "search")),
    (("read",), ("读取", "阅读", "read")),
    (("write",), ("写入", "保存", "write")),
    (("edit",), ("编辑", "edit")),
)


def verb_intent_boost(ql: str, nl: str) -> float:
    name_tokens = nl.split("_")
    for en_tokens, cn_words in VERB_INTENT:
        if any(w in ql for w in cn_words) and any(t in name_tokens for t in en_tokens):
            return 0.5
    return 0.0


def haystack_for(tool: Any, desc_cap: int) -> str:
    name = str(getattr(tool, "name", "") or "")
    desc = str(getattr(tool, "description", "") or "")
    if len(desc) > desc_cap:
        desc = desc[:desc_cap]
    return f"{name} {desc} {parameters_to_text(getattr(tool, 'parameters', None))}"


def dense_search(
    query: str,
    tools: List[Any],
    model: Any,
    embedding_cache: Dict[str, Any],
    *,
    limit: int,
    detail_level: int,
    desc_cap: int = 256,
    min_sim: float = -1.0,
) -> List[dict]:
    if model is None or not (tools or []):
        return []
    import numpy as np

    ql = (query or "").strip().lower()
    ql_norm = "_".join(ql.split())
    try:
        emb_results = list(model.embed([ql]))
    except Exception as exc:
        logger.warning("[tool_retrieval] query embed failed: %s", exc)
        return []
    if not emb_results:
        return []
    qv = emb_results[0]
    qn = float(np.linalg.norm(qv))
    scored = []
    for tool in (tools or []):
        name = str(getattr(tool, "name", "") or "")
        nl = name.lower()
        tv = embedding_cache.get(name)
        if tv is None:
            tv = embed_single(tool, model, desc_cap=desc_cap)
            if tv is not None:
                embedding_cache[name] = tv
        if tv is None:
            continue
        tn = float(np.linalg.norm(tv))
        if tn == 0 or qn == 0:
            continue
        sim = float(np.dot(qv, tv) / (qn * tn))
        if ql == nl or ql_norm == nl:
            sim += 1.0
        elif len(nl) >= 5 and (nl in ql_norm or nl in ql):
            sim += 0.8
        elif len(ql) >= 3 and (nl.startswith(ql) or nl.startswith(ql_norm)):
            sim += 0.3
        else:
            name_tokens = [t for t in nl.split("_") if len(t) >= 3]
            if name_tokens:
                hit = sum(1 for t in name_tokens if t in ql or t in ql_norm or t.rstrip("s") in ql)
                if hit:
                    ratio = hit / len(name_tokens)
                    sim += 0.5 if hit == len(name_tokens) else 0.25 * ratio
            elif len(ql) >= 4 and (ql in nl or ql_norm in nl):
                sim += 0.2
        sim += verb_intent_boost(ql, nl)
        scored.append((sim, tool))
    scored.sort(key=lambda item: (-item[0], getattr(item[1], "name", "")))
    matched = [tool for sim, tool in scored if sim >= min_sim][:limit]
    return [build_tool_summary(tool, detail_level=detail_level) for tool in matched]


def precompute_embeddings(
    tools: List[Any],
    model: Any,
    embedding_cache: Dict[str, Any],
    *,
    desc_cap: int = 256,
) -> None:
    if model is None or not tools:
        return
    haystacks = [haystack_for(t, desc_cap) for t in tools]
    try:
        embs = list(model.embed(haystacks))
        embedding_cache.clear()
        embedding_cache.update(
            {str(getattr(tools[i], "name", "")): embs[i] for i in range(len(tools))}
        )
        logger.info("[tool_retrieval] pre-computed %d embeddings", len(embedding_cache))
    except Exception as exc:
        logger.warning("[tool_retrieval] pre-compute failed: %s", exc)
        embedding_cache.clear()


def embed_single(tool: Any, model: Any, *, desc_cap: int = 256):
    if model is None:
        return None
    try:
        h = haystack_for(tool, desc_cap)
        return list(model.embed([h]))[0]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# v2: hybrid (BM25 + dense RRF) + method dispatch with degradation
# ---------------------------------------------------------------------------

def hybrid_search(
    query: str,
    tools: List[Any],
    model: Any,
    embedding_cache: Dict[str, Any],
    bm25_index: Any,
    *,
    limit: int,
    detail_level: int,
    desc_cap: int = 256,
    min_sim: float = 0.0,
    rrf_k: int = 60,
) -> List[dict]:
    """Hybrid retrieval: BM25 + dense fused via Reciprocal Rank Fusion.

    RRF_score(tool) = 1/(rrf_k + rank_bm25) + 1/(rrf_k + rank_dense)

    Uses only ranks, not raw scores — scale-free (BM25 scores and cosine
    similarities are not comparable, so weighted blending would be wrong).
    ratel ``fusion.rs`` uses the same RRF(k=60).

    Each ranker returns up to ``limit*3`` candidates (overflow so fusion
    has a pool larger than the final limit to fill). A tool only present in
    one ranker still contributes one RRF term; tools present in both get two.
    """
    from .bm25_search import bm25_search as _bm25_search

    pool_size = max(limit * 3, limit + 10)

    # BM25 candidates (pure-text, never fails)
    bm25_results = _bm25_search(
        query, tools, bm25_index,
        limit=pool_size, detail_level=detail_level,
        desc_cap=desc_cap, min_sim=0.0,
    )

    # Dense candidates (may fail / be empty if model not ready)
    dense_results: List[dict] = []
    if model is not None:
        try:
            dense_results = dense_search(
                query, tools, model, embedding_cache,
                limit=pool_size, detail_level=detail_level,
                desc_cap=desc_cap, min_sim=min_sim,
            )
        except Exception as exc:
            logger.warning("[tool_retrieval] hybrid dense leg failed: %s", exc)
            dense_results = []

    # If dense failed and BM25 also returned nothing, give up.
    if not bm25_results and not dense_results:
        return []

    # If dense leg failed entirely, just return BM25 ranking (degrade).
    if not dense_results:
        return bm25_results[:limit]

    # RRF fusion. rank is 0-based; rrf term = 1/(k + rank). Build name→score.
    rrf: Dict[str, float] = {}
    for rank, r in enumerate(bm25_results):
        n = str(r.get("name", "") or "")
        if n:
            rrf[n] = rrf.get(n, 0.0) + 1.0 / (rrf_k + rank)
    for rank, r in enumerate(dense_results):
        n = str(r.get("name", "") or "")
        if n:
            rrf[n] = rrf.get(n, 0.0) + 1.0 / (rrf_k + rank)

    # Order by fused score; tie-break by name for determinism.
    ordered_names = sorted(rrf.keys(), key=lambda n: (-rrf[n], n))

    # Materialize from the dense results (they carry full detail_level=3
    # schema in v3; fall back to bm25_results if a name only BM25 had).
    by_name = {str(r.get("name", "") or ""): r for r in dense_results}
    by_name_bm25 = {str(r.get("name", "") or ""): r for r in bm25_results}
    out: List[dict] = []
    for n in ordered_names[:limit]:
        if n in by_name:
            out.append(by_name[n])
        elif n in by_name_bm25:
            out.append(by_name_bm25[n])
    return out


def search(
    query: str,
    tools: List[Any],
    *,
    method: str,
    embedding_model: Any,
    embedding_cache: Dict[str, Any],
    bm25_index: Any,
    limit: int,
    detail_level: int,
    desc_cap: int = 256,
    min_sim: float = 0.35,
    rrf_k: int = 60,
) -> List[dict]:
    """Dispatch retrieval by ``method`` with a degradation chain.

    method=bm25      → pure BM25 (no model, never fails)
    method=semantic  → pure dense (model None → degrade to BM25, not [])
    method=hybrid    → BM25 + dense RRF (model None → degrade to pure BM25)

    The degradation rule: when ``embedding_model`` is unavailable, any
    method degrades to BM25 rather than returning ``[]``. This replaces the
    old ``_embedding_model is None: return []`` paralysis.
    """
    method = (method or "hybrid").strip().lower()
    if method not in {"bm25", "semantic", "hybrid"}:
        logger.warning("[tool_retrieval] unknown method %r, falling back to hybrid", method)
        method = "hybrid"

    embedding_ok = embedding_model is not None

    # Degradation: any method → BM25 when embedding unavailable.
    if not embedding_ok:
        if method != "bm25":
            logger.warning(
                "[tool_retrieval] embedding unavailable (method=%s); degrading to BM25",
                method,
            )
        return _bm25_dispatch(query, tools, bm25_index,
                             limit=limit, detail_level=detail_level,
                             desc_cap=desc_cap, min_sim=0.0)

    if method == "bm25":
        # Explicit bm25 even when embedding is available (offline / reproducible).
        return _bm25_dispatch(query, tools, bm25_index,
                             limit=limit, detail_level=detail_level,
                             desc_cap=desc_cap, min_sim=0.0)
    if method == "semantic":
        return dense_search(
            query, tools, embedding_model, embedding_cache,
            limit=limit, detail_level=detail_level,
            desc_cap=desc_cap, min_sim=min_sim,
        )
    # hybrid
    return hybrid_search(
        query, tools, embedding_model, embedding_cache, bm25_index,
        limit=limit, detail_level=detail_level,
        desc_cap=desc_cap, min_sim=min_sim, rrf_k=rrf_k,
    )


def _bm25_dispatch(query, tools, bm25_index, *, limit, detail_level, desc_cap, min_sim):
    """Build-on-the-fly fallback if no cached index (slow path)."""
    from .bm25_search import bm25_search as _bm25_search
    return _bm25_search(
        query, tools, bm25_index,
        limit=limit, detail_level=detail_level,
        desc_cap=desc_cap, min_sim=min_sim,
    )
