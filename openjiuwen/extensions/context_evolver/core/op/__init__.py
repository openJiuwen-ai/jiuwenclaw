# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Operation system for composable workflows."""

from .base_op import BaseOp
from .sequential_op import SequentialOp
from .parallel_op import ParallelOp

__all__ = ["BaseOp", "SequentialOp", "ParallelOp"]
