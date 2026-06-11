# Retrieval Algorithm

## Problem Definition

Given:

- a loaded offline index
- a user query
- a progressive tree retriever

Return:

- the best matching executable payloads in ranked order

## Retrieval Policy

The dispatch package currently loads the progressive tree artifacts produced by `indexing/`:

- `tree_index.yaml`
- `catalog.jsonl`
- `manifest.json`

Online retrieval runs progressive tree routing over those artifacts and returns the selected leaf payloads.

## Progressive Tree Retrieval

Input:

- query
- tree root
- `top_k`

Process:

1. start from the current visible subtree
2. if the structure is trivial, use deterministic shortcuts
3. otherwise ask the LLM to choose among the current visible boundary nodes
4. recurse into selected branches or terminate on selected items
5. reduce branch results to the requested `top_k`

Important rules:

- every LLM routing decision sees only the current visible subtree
- the model outputs the visible boundary node display names only
- display names are uniquified automatically if collisions exist
- single-candidate situations do not call the LLM

## Main Implementations

- `retrieval/tree/progressive.py`
- `retrieval/service/retriever.py`
