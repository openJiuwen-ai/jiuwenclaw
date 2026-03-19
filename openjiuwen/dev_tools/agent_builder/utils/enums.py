# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum
from typing import Literal


class AgentType(str, Enum):
    LLM_AGENT = "llm_agent"
    WORKFLOW = "workflow"


class BuildState(str, Enum):
    INITIAL = "initial"
    PROCESSING = "processing"
    COMPLETED = "completed"


class ProgressStage(str, Enum):
    # Common
    INITIALIZING = "initializing"
    CLARIFYING = "clarifying"
    RESOURCE_RETRIEVING = "resource_retrieving"
    COMPLETED = "completed"
    ERROR = "error"
    OPTIMIZING = "optimizing"

    # LLM Agent
    GENERATING_CONFIG = "generating_config"
    TRANSFORMING_DSL = "transforming_dsl"

    # Workflow Agent
    DETECTING_INTENTION = "detecting_intention"
    GENERATING_WORKFLOW_DESIGN = "generating_workflow_design"
    GENERATING_DL = "generating_dl"
    VALIDATING_DL = "validating_dl"
    REFINING_DL = "refining_dl"
    TRANSFORMING_MERMAID = "transforming_mermaid"
    TRANSFORMING_WORKFLOW_DSL = "transforming_workflow_dsl"


class ProgressStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    WARNING = "warning"


AgentTypeLiteral = Literal["llm_agent", "workflow"]
