"""BM25 tool-retrieval algorithm core. 0 agent-core dependency.

Pure-Python algorithm that operates on duck-typed tool objects
(``getattr(name/description/parameters)``) and accepts the agent-core
base-class helpers (``parameters_to_text_fn``, ``build_tool_summary_fn``) as
injected callables. This keeps the module import-clean of ``openjiuwen`` — the
rail adapter wires the agent-core helpers in at the boundary, so behavior is
identical to the in-rail implementation (no reimplementation drift).

v3 removed the dense/embedding path (fastembed + bge-small too big to deploy,
BM25+CJK n-gram covers Chinese recall without a model). This package is now
BM25-only.
"""

from .search import (
    haystack_for,
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
    "haystack_for",
    "dispatch_search",
    "parameters_to_text",
    "parameters_summary",
    "safe_serialize_parameters",
    "build_tool_summary",
    "filter_executable",
    "split_identifier",
    "flatten_schema",
    "bm25_tokenize",
    "BM25Okapi",
    "BM25Index",
    "build_bm25_index",
    "bm25_search",
]
