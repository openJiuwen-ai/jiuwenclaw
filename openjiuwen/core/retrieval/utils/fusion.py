# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Fusion Utility Functions

Contains fusion algorithms such as RRF (Reciprocal Rank Fusion).
"""

from collections import defaultdict
from typing import Dict, List

from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult, SearchResult


def rrf_fusion(
    results_list: List[List[RetrievalResult]],
    k: int = 60,
) -> List[RetrievalResult | SearchResult]:
    """
    Reciprocal Rank Fusion (RRF) - fuse multiple retrieval results

    Args:
        results_list: List of multiple retrieval result lists
        k: RRF parameter, default 60

    Returns:
        Fused retrieval result list
    """
    # Use dictionary to store score for each result
    score_dict: Dict[str, float] = defaultdict(float)
    result_dict: Dict[str, RetrievalResult] = {}

    # Fuse each result list
    for results in results_list:
        for rank, result in enumerate(results, start=1):
            # Use text as unique identifier
            key = result.text
            # RRF score calculation
            score_dict[key] += 1.0 / (k + rank)
            # Save result object (preserve metadata from first occurrence)
            if key not in result_dict:
                result_dict[key] = result

    # Sort by score
    sorted_items = sorted(score_dict.items(), key=lambda x: x[1], reverse=True)

    # Build fused result list
    fused_results = []
    for key, score in sorted_items:
        result = result_dict[key]
        # Update score to fused score
        result.score = score
        fused_results.append(result)

    return fused_results
