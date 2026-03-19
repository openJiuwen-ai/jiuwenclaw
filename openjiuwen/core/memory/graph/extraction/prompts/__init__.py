# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Extraction Prompts Package

Prompt template manager and entity-extraction prompt loading.
"""

__all__ = ["TemplateManager"]
from .manager import ThreadSafePromptManager as TemplateManager
