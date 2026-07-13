# Retrieval

## Purpose

`retrieval/` owns online retrieval.

It loads offline artifacts built by `indexing/`, runs retrieval over them, and returns ordered candidates or payloads.

## Canonical Online Route

The canonical route uses the progressive tree index:

1. load `tree_index.yaml` and `catalog.jsonl`
2. route the query through the progressive tree
3. return selected leaf payloads from the catalog

## Package Layout

### `retrieval/io/`

- loads tree and catalog artifacts

### `retrieval/tree/`

- progressive tree search
- disclosure decisions
- branch reduction
- trace generation

### `retrieval/protocols/`

- prompt generation
- display-name normalization
- output parsing

### `retrieval/service/`

- high-level retriever interfaces

## Main Entry Points

- `retrieval/service/retriever.py`
- `retrieval/tree/progressive.py`

Typical usage:

```python
from retrieval.service.retriever import Retriever

retriever = Retriever.from_index("/abs/path/to/index")
payloads = retriever.search("find tools for browser automation", top_k=5)
```

## Dependency Boundary

`retrieval/` does not import orchestration core runtime. Orchestration consumes retrieval through canonical retrieval modules and the retrieval adapter layer under `orchestration/`.
