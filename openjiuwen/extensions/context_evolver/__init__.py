# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Context Evolver extension for task memory management.

Public API re-exports for simplified imports:
    from openjiuwen.extensions.context_evolver import TaskMemoryService, AddMemoryRequest
"""

from openjiuwen.extensions.context_evolver.service.task_memory_service import (
    TaskMemoryService,
    AddMemoryRequest,
)

from openjiuwen.extensions.context_evolver.context_evolving_react_agent import (
    ContextEvolvingReActAgent,
    create_memory_agent_config,
    MemoryAgentConfigInput,
    SummarizeTrajectoriesInput,
)

from openjiuwen.extensions.context_evolver.tool.wikipedia_tool import wikipedia_tool

from openjiuwen.extensions.context_evolver.core.file_connector import (
    JSONFileConnector,
    safe_model_dump,
)

from openjiuwen.extensions.context_evolver.core.vector_store import MemoryVectorStore

from openjiuwen.extensions.context_evolver.schema.io_schema import (
    ACEMemory,
    ACERetrievedMemory,
    ReasoningBankMemory,
    ReasoningBankMemoryItem,
    ReasoningBankRetrievedMemory,
    ReMeMemory,
    ReMeMemoryMetadata,
    ReMeRetrievedMemory,
    SummarizeResponse,
    RetrieveResponse,
)

__all__ = [
    "TaskMemoryService",
    "AddMemoryRequest",
    "ContextEvolvingReActAgent",
    "create_memory_agent_config",
    "MemoryAgentConfigInput",
    "SummarizeTrajectoriesInput",
    "wikipedia_tool",
    "JSONFileConnector",
    "safe_model_dump",
    "MemoryVectorStore",
    "ACEMemory",
    "ACERetrievedMemory",
    "ReasoningBankMemory",
    "ReasoningBankMemoryItem",
    "ReasoningBankRetrievedMemory",
    "ReMeMemory",
    "ReMeMemoryMetadata",
    "ReMeRetrievedMemory",
    "SummarizeResponse",
    "RetrieveResponse",
]
