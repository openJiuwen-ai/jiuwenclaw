# -*- coding: utf-8 -*-
"""BM25 retrieval entry point + haystack builder. No agent-core dependency,
fully self-contained.

Operates on duck-typed tool objects (``getattr(name/description/parameters)``).
Haystack building uses the locally-ported ``summary`` helpers (verbatim from
agent-core's base class) — no injected callables, so the module runs standalone
(can be unit-tested / reused outside agent-core).

v3 removed the dense/embedding path (fastembed + bge-small ~90MB too big to
deploy, and BM25+CJK n-gram already covers Chinese recall without a model).
This module now only provides the BM25 haystack + a thin ``search`` dispatch
that delegates to ``bm25_search.bm25_search``.
"""
from __future__ import annotations

import logging
from typing import Any, List

logger = logging.getLogger("jiuwenswarm.common.tool_retrieval.search")


def haystack_for(tool: Any, desc_cap: int) -> str:
    """Build the searchable text for a tool.

    v2 (phase 2): cleaned haystack — name (split) + description + clean
    schema tokens (field names, enum values; JSON structural noise like
    ``type``/``properties``/``required`` excluded).

    BM25 ranks over this. A clean haystack is the precondition for BM25 to
    score well: the old raw-dict haystack drowned the signal field names in
    JSON noise and left composite names (``memory_search``) unsplit, which
    bag-of-words rankers can't match on.

    NOTE: ``build_tool_summary`` (the full schema returned to the LLM so it
    can construct arguments) is NOT touched — it must stay complete. Only
    the search index uses this cleaned text.
    """
    from .summary import _split_identifier, _flatten_schema

    tokens: List[str] = []
    name = str(getattr(tool, "name", "") or "")
    if name:
        tokens.append(name)                       # raw name for exact-match boost
        split = _split_identifier(name)
        if split and split != name.lower():
            tokens.append(split)                   # memory_search -> "memory search"
    desc = str(getattr(tool, "description", "") or "")
    if desc:
        if len(desc) > desc_cap:
            desc = desc[:desc_cap]
        tokens.append(desc)
    _flatten_schema(getattr(tool, "parameters", None), tokens)
    return " ".join(tokens)


def search(
    query: str,
    tools: List[Any],
    *,
    bm25_index: Any,
    limit: int,
    detail_level: int,
    desc_cap: int = 256,
) -> List[dict]:
    """BM25-only retrieval dispatch.

    Builds the index lazily if missing (slow path; normally pre-built by the
    rail in ``before_invoke``). Pure-text — no model, never fails, never
    returns ``[]`` for a non-empty corpus.
    """
    from .bm25_search import bm25_search as _bm25_search
    return _bm25_search(
        query, tools, bm25_index,
        limit=limit, detail_level=detail_level,
        desc_cap=desc_cap, min_sim=0.0,
    )
