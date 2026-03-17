# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Milvus Graph Store

Provide milvus database support for graph-structured vector storage and retrieval capabilities
"""

__all__ = ["MilvusGraphStore", "register_milvus_support"]

from threading import Lock

from pymilvus import RRFRanker, WeightedRanker

from openjiuwen.core.foundation.store.graph.result_ranking import register_result_ranker_cls

from ..base import GraphStoreFactory
from .milvus_support import MilvusGraphStore

MILVUS_SUPPORT_REGISTERED: bool = False
_MILVUS_SUPPORT_REGISTER_LOCK = Lock()


def register_milvus_support():
    """Register Milvus Support, does nothing if registration is already done"""
    global MILVUS_SUPPORT_REGISTERED

    with _MILVUS_SUPPORT_REGISTER_LOCK:
        if not MILVUS_SUPPORT_REGISTERED:
            GraphStoreFactory.register_backend("milvus", MilvusGraphStore)
            register_result_ranker_cls(name="milvus", weighted=WeightedRanker, rrf=RRFRanker)
            MILVUS_SUPPORT_REGISTERED = True


register_milvus_support()
