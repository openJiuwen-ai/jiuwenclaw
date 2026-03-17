# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Operator abstraction for atomic execution and optimization.

Includes:
- Operator, TunableSpec: Base class and tunable parameter definition
- LLMCallOperator: LLM invocation with prompt tunables
- ToolCallOperator: Tool execution with enabled/retries tunables
- MemoryCallOperator: Memory read/write with enabled/retries tunables
"""

from openjiuwen.core.operator.base import Operator, TunableSpec
from openjiuwen.core.operator.llm_call import LLMCallOperator, LLMCall
from openjiuwen.core.operator.tool_call import ToolCallOperator
from openjiuwen.core.operator.memory_call import MemoryCallOperator

__all__ = [
    "Operator",
    "TunableSpec",
    "LLMCallOperator",
    "LLMCall",
    "ToolCallOperator",
    "MemoryCallOperator",
]
