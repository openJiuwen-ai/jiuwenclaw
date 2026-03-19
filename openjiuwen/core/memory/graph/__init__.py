# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph memory: knowledge-graph-based memory over conversations and documents.

This module maintains a memory graph of entities, relations, and episodes.
It uses LLMs to extract entities and relations from user conversations and
documents, merges and deduplicates them with existing graph data, and supports
configurable hybrid (semantic + full text) search over entities, relations, and
episodes (with optional reranking).
"""
