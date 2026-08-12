# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.schema.config import ContextEngineConfig
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ContextWindowChange, ModelContext, ContextStats, ContextWindow
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context_engine import ContextEngine

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.token.base import TokenCounter
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.token.tiktoken_counter import TiktokenCounter

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.base import ContextProcessor
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.offloader.message_offloader import (
    MessageOffloader,
    MessageOffloaderConfig
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.offloader.tool_result_budget_processor import (
    ToolResultBudgetProcessor,
    ToolResultBudgetProcessorConfig,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.offloader.tool_result_window_processor import (
    ToolResultWindowProcessor,
    ToolResultWindowProcessorConfig,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.offloader.message_summary_offloader import (
    MessageSummaryOffloader,
    MessageSummaryOffloaderConfig
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.compressor.micro_compact_processor import (
    MicroCompactProcessor,
    MicroCompactProcessorConfig,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.compressor.dialogue_compressor import (
    DialogueCompressor,
    DialogueCompressorConfig
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.compressor.current_round_compressor import (
    CurrentRoundCompressor,
    CurrentRoundCompressorConfig
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.compressor.round_level_compressor import (
    RoundLevelCompressor,
    RoundLevelCompressorConfig
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.compressor.full_compact_processor import (
    FullCompactProcessor,
    FullCompactProcessorConfig,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.compressor.reasoning_tool_loop_compact_processor import (
    ReasoningToolLoopCompactProcessor,
    ReasoningToolLoopCompactProcessorConfig,
)

# context base classes
_CORE_CLASSES = [
    "ContextEngineConfig",
    "ContextWindow",
    "ContextWindowChange",
    "ModelContext",
    "ContextStats",
    "ContextEngine"
]


_TOKEN_COUNTER = [
    "TokenCounter",
    "TiktokenCounter"
]


_PROCESSORS_CLASSES = [
    # base process class
    "ContextProcessor",
    "ToolResultBudgetProcessor",
    "ToolResultBudgetProcessorConfig",
    # tool result window
    "ToolResultWindowProcessor",
    "ToolResultWindowProcessorConfig",
    # message offloader
    "MessageOffloader",
    "MessageOffloaderConfig",
    # message summary offloader
    "MessageSummaryOffloader",
    "MessageSummaryOffloaderConfig",
    # micro compact
    "MicroCompactProcessor",
    "MicroCompactProcessorConfig",
    # dialogue compressor
    "DialogueCompressor",
    "DialogueCompressorConfig",
    # current round compressor
    "CurrentRoundCompressor",
    "CurrentRoundCompressorConfig",
    # round level compressor
    "RoundLevelCompressor",
    "RoundLevelCompressorConfig",
    # full compact processor
    "FullCompactProcessor",
    "FullCompactProcessorConfig",
    # reasoning + tool-call loop compact
    "ReasoningToolLoopCompactProcessor",
    "ReasoningToolLoopCompactProcessorConfig",
]


# Combine all public APIs
__all__ = (
    _CORE_CLASSES
    + _TOKEN_COUNTER
    + _PROCESSORS_CLASSES
)
