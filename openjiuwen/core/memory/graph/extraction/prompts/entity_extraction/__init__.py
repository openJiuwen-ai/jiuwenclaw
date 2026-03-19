# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Entity Extraction Prompts

Language registration and formatting helpers for entity extraction prompts.
"""

__all__ = ["ensure_valid_language", "format_relation_definitions", "get_formatting_kwargs"]

from . import cn, en
from .base import ensure_valid_language, format_relation_definitions, get_formatting_kwargs

cn.register_language()
en.register_language()
