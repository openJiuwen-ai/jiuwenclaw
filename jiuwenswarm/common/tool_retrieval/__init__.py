"""Dense tool-retrieval algorithm core. 0 agent-core dependency.

Mirrors the layering of ``common/tokenjuice/``: pure-Python algorithm that
operates on duck-typed tool objects (``getattr(name/description/parameters)``)
and accepts the two agent-core base-class helpers
(``parameters_to_text_fn``, ``build_tool_summary_fn``) as injected callables.
This keeps the module import-clean of ``openjiuwen`` — the rail adapter wires
the agent-core helpers in at the boundary, so behavior is identical to the
in-rail implementation (no reimplementation drift).
"""

from .embedder import ensure_embedding_model, embed_texts
from .search import (
    VERB_INTENT,
    verb_intent_boost,
    haystack_for,
    dense_search,
    precompute_embeddings,
    embed_single,
    hybrid_search,
    search as dispatch_search,
)
from .summary import (
    parameters_to_text,
    parameters_summary,
    safe_serialize_parameters,
    build_tool_summary,
    _split_identifier as split_identifier,
    _flatten_schema as flatten_schema,
)
from .corpus import filter_executable
from .bm25_search import (
    tokenize as bm25_tokenize,
    BM25Okapi,
    BM25Index,
    build_bm25_index,
    bm25_search,
)

__all__ = [
    "ensure_embedding_model",
    "embed_texts",
    "VERB_INTENT",
    "verb_intent_boost",
    "haystack_for",
    "dense_search",
    "precompute_embeddings",
    "embed_single",
    "parameters_to_text",
    "parameters_summary",
    "safe_serialize_parameters",
    "build_tool_summary",
    "filter_executable",
    "split_identifier",
    "flatten_schema",
    # BM25 + hybrid (v2)
    "bm25_tokenize",
    "BM25Okapi",
    "BM25Index",
    "build_bm25_index",
    "bm25_search",
    "hybrid_search",
    "dispatch_search",
]
