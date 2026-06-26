# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""tokenjuice — deterministic shell output compression for LLM agents.

Python port of https://github.com/vincentkoc/tokenjuice

Usage::

    from jiuwenswarm.common.tokenjuice import reduce_execution, ToolExecutionInput

    result = reduce_execution(ToolExecutionInput(
        tool_name="exec",
        command="git status",
        stdout="On branch main\\nnothing to commit, working tree clean\\n",
        exit_code=0,
    ))

    print(result.inline_text)   # "working tree clean"
    print(result.stats)         # {"raw_chars": 48, "reduced_chars": 18, "ratio": 0.375}
"""

__all__ = [
    "reduce_execution",
    "load_rules",
    "ClassificationResult",
    "CompactResult",
    "CompiledRule",
    "JsonRule",
    "ReduceOptions",
    "ToolExecutionInput",
]

from .reduce import reduce_execution
from .rules import load_rules
from .types import (
    ClassificationResult,
    CompactResult,
    CompiledRule,
    JsonRule,
    ReduceOptions,
    ToolExecutionInput,
)
