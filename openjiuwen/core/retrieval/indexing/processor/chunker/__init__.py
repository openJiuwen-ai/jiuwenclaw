# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Chunker package: split documents into chunks for indexing.

**SDK users**
- Use a built-in chunker by name: ``get_chunker("char", chunk_size=512)`` or
  ``get_chunker("hybrid", chunk_size=512, chunk_overlap=50)`` (recommended for
  mixed Word + Excel).
- Or instantiate directly: ``HybridChunker(inner_chunker=CharChunker(...))``.

**Contributors**
- Implement :class:`Chunker` and pass your instance to ``KnowledgeBase(chunker=...)``.
- To expose your chunker by name (e.g. for config-driven pipelines), call
  :func:`register_chunker` with a name and your class or a factory ``(**kwargs) -> Chunker``.
"""

from typing import Any, Callable, Dict, Type, Union

from openjiuwen.core.retrieval.indexing.processor.chunker.base import Chunker
from openjiuwen.core.retrieval.indexing.processor.chunker.char_chunker import CharChunker
from openjiuwen.core.retrieval.indexing.processor.chunker.hybrid_chunker import HybridChunker

# Name -> Chunker class or factory callable (**kwargs) -> Chunker
ChunkerEntry = Union[Type[Chunker], Callable[..., Chunker]]
CHUNKER_REGISTRY: Dict[str, ChunkerEntry] = {}


def _hybrid_factory(**kwargs: Any) -> Chunker:
    """
    Build HybridChunker.

    Supported kwargs:
        - inner_chunker: Optional[Chunker]. If omitted, a CharChunker is created from
          chunk_size/chunk_overlap (any extra kwargs are passed to CharChunker).
        - chunk_size: int (used only when inner_chunker is not provided)
        - chunk_overlap: int (used only when inner_chunker is not provided)
        - no_split_when: callable predicate for one-chunk documents
        - Any other kwargs: forwarded to the default CharChunker when inner_chunker
          is omitted (for forward compatibility); otherwise raise TypeError.
    """
    params = dict(kwargs)
    inner_chunker = params.pop("inner_chunker", None)
    chunk_size = params.pop("chunk_size", 512)
    chunk_overlap = params.pop("chunk_overlap", 50)
    no_split_when = params.pop("no_split_when", None)

    if inner_chunker is None:
        # Pass any remaining kwargs to the default CharChunker for forward compatibility
        inner_chunker = CharChunker(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap, **params
        )
    elif params:
        unknown = ", ".join(sorted(params.keys()))
        raise TypeError(f"Unknown kwargs for 'hybrid' chunker: {unknown}")
    elif not isinstance(inner_chunker, Chunker):
        raise TypeError("inner_chunker must be a Chunker instance")

    return HybridChunker(inner_chunker=inner_chunker, no_split_when=no_split_when)


def register_chunker(
    name: str,
    chunker_class_or_factory: ChunkerEntry,
    overwrite: bool = False,
) -> None:
    """
    Register a chunker by name so it can be obtained via :func:`get_chunker`.

    Args:
        name: Identifier (e.g. ``"char"``, ``"hybrid"``, or your custom name).
        chunker_class_or_factory: A :class:`Chunker` subclass (instantiated with
            ``**kwargs``) or a callable ``(**kwargs) -> Chunker``.
        overwrite: Whether to overwrite an existing registration. Defaults to False.

    Raises:
        ValueError: If name is empty or already registered and overwrite is False.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("chunker name must be a non-empty string")
    if name in CHUNKER_REGISTRY and not overwrite:
        raise ValueError(
            f"Chunker '{name}' is already registered. "
            "Use overwrite=True to replace it."
        )
    CHUNKER_REGISTRY[name] = chunker_class_or_factory


def get_chunker(name: str, **kwargs: Any) -> Chunker:
    """
    Get a chunker instance by name.
    Built-in names: ``"char"``, ``"hybrid"``.

    Args:
        name: Registered name (e.g. ``"hybrid"`` for mixed Word/Excel).
        **kwargs: Passed to the chunker constructor or factory (e.g. ``chunk_size``,
            ``chunk_overlap``, ``no_split_when`` for ``"hybrid"``).

    Returns:
        Chunker instance.

    Raises:
        KeyError: If ``name`` is not registered.
    """
    if name not in CHUNKER_REGISTRY:
        raise KeyError(f"Unknown chunker: {name}. Registered: {list(CHUNKER_REGISTRY.keys())}")
    entry = CHUNKER_REGISTRY[name]
    chunker = entry(**kwargs)
    if not isinstance(chunker, Chunker):
        raise TypeError(
            f"Chunker entry '{name}' must return a Chunker instance, got {type(chunker).__name__}"
        )
    return chunker


# Built-in registrations
register_chunker("char", CharChunker)
register_chunker("hybrid", _hybrid_factory)

__all__ = [
    "Chunker",
    "CharChunker",
    "HybridChunker",
    "CHUNKER_REGISTRY",
    "register_chunker",
    "get_chunker",
]
