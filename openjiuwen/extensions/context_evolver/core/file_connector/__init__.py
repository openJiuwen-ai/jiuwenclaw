# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""File connector module for generic persistence operations."""

from .json_file_connector import (
    JSONFileConnector,
    safe_model_dump,
)

__all__ = ["JSONFileConnector", "safe_model_dump"]
