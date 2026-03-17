# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph Store Constants

Defines constant values for graph store that should not be changed.
"""

# Collection name for vector database backend
ENTITY_COLLECTION: str = "ENTITY_COLLECTION"
RELATION_COLLECTION: str = "RELATION_COLLECTION"
EPISODE_COLLECTION: str = "EPISODE_COLLECTION"

# Validation limit for graph store's vector database config
VARCHAR_LIMIT = dict(gt=1, le=65535)
ARRAY_LIMIT = dict(gt=1, le=4096)

# Default limitation for number of workers in embedding tasks of graph store
DEFAULT_WORKER_NUM = 10
