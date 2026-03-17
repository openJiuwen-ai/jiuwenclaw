# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Utility Functions for Embedding Models

Includes helper methods required to support subclasses of Embedding
"""

import base64

import numpy as np


def parse_base64_embedding(b64_embedding: str) -> list[float]:
    """Parse base64 embeddings into list[float]"""
    decoded_bytes = base64.b64decode(b64_embedding)
    return np.frombuffer(decoded_bytes, dtype=np.float32).tolist()
