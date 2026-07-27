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
)
from .summary import (
    parameters_to_text,
    parameters_summary,
    safe_serialize_parameters,
    build_tool_summary,
)
from .corpus import filter_executable

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
]
