# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Hyperparameter defaults and valid ranges for self-evolving training.
"""

from dataclasses import dataclass


@dataclass
class TuneConstant:
    """Hyperparameter defaults and validation bounds.

    Attributes:
        default_example_num: Number of examples per iteration
        default_iteration_num: Default training iterations
        default_max_sampled_example_num: Max examples to sample
        default_parallel_num: Default parallelism for inference
        default_max_num_sample_error_cases: Max error cases to log
        default_early_stop_score: Score threshold for early stopping
        min_iteration_num: Minimum iterations allowed
        max_iteration_num: Maximum iterations allowed
        min_parallel_num: Minimum parallelism allowed
        max_parallel_num: Maximum parallelism allowed
        min_example_num: Minimum examples allowed
        max_example_num: Maximum examples allowed
    """

    # Default values
    default_example_num: int = 1
    default_iteration_num: int = 3
    default_max_sampled_example_num: int = 10
    default_parallel_num: int = 1
    default_max_num_sample_error_cases: int = 10
    default_early_stop_score: float = 1.0

    # Valid ranges
    min_iteration_num: int = 1
    max_iteration_num: int = 20
    min_parallel_num: int = 1
    max_parallel_num: int = 20
    min_example_num: int = 0
    max_example_num: int = 20
