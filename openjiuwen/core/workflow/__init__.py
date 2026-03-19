# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.workflow.base import (
    WorkflowExecutionState,
    WorkflowOutput,
    WorkflowChunk,
    generate_workflow_key,
    WorkflowCard,

)
from openjiuwen.core.workflow.workflow import Workflow

from openjiuwen.core.workflow.workflow_config import (
    WorkflowConfig,
)

from openjiuwen.core.workflow.components.component import (
    ComponentExecutable,
    ComponentComposable,
    WorkflowComponent,
    Input,
    Output,
)

from openjiuwen.core.workflow.components.base import (
    ComponentAbility,
    WorkflowComponentMetadata,
    ComponentConfig,
    ComponentState,
)

from openjiuwen.core.workflow.components.flow.workflow_comp import SubWorkflowComponent
from openjiuwen.core.workflow.components.flow.start_comp import Start
from openjiuwen.core.workflow.components.flow.end_comp import End, EndConfig
from openjiuwen.core.workflow.components.flow.branch_comp import BranchComponent
from openjiuwen.core.workflow.components.flow.loop.loop_comp import (LoopComponent, LoopGroup,
     LoopSetVariableComponent, LoopBreakComponent)
from openjiuwen.core.workflow.components.llm.llm_comp import LLMComponent, LLMCompConfig
from openjiuwen.core.workflow.components.llm.questioner_comp import (
    QuestionerComponent,
    QuestionerConfig,
    FieldInfo
)
from openjiuwen.core.workflow.components.llm.intent_detection_comp import (
    IntentDetectionComponent,
    IntentDetectionCompConfig
)
from openjiuwen.core.workflow.components.resource.knowledge_retrieval_comp import (
    KnowledgeRetrievalComponent,
    KnowledgeRetrievalCompConfig,
)
from openjiuwen.core.workflow.components.tool.tool_comp import ToolComponent, ToolComponentConfig
from openjiuwen.core.workflow.components.tool.http import (
    HTTPRequestComponent,
    HttpComponentConfig,
    HttpRequestParamConfig,
    HttpAuthConfig,
    HttpRequestBodyConfig,
    HttpResponseHandlingConfig,
    HttpAdvancedOptionsConfig,
    HttpRetryConfig,
    HttpRateLimitConfig,
    HttpAuthType,
    HttpContentType,
    HttpResponseFormat
)
from openjiuwen.core.workflow.components.flow.branch_router import BranchRouter, Branch
from openjiuwen.core.workflow.components.condition.condition import Condition, FuncCondition, AlwaysTrue
from openjiuwen.core.workflow.components.condition.expression import ExpressionCondition
from openjiuwen.core.workflow.components.condition.array import ArrayCondition
from openjiuwen.core.workflow.components.condition.number import NumberCondition

from openjiuwen.core.session.workflow import Session, create_workflow_session
from openjiuwen.core.workflow._workflow import execute_single_component


_WORKFLOW_CLASSES = [
    "Workflow",
    "WorkflowCard",
    "WorkflowOutput",
    "WorkflowChunk",
    "WorkflowExecutionState",
    "execute_single_component"
]

_WORKFLOW_METHODS = [
    "generate_workflow_key"
]

_COMPONENTS_CLASSES = [
    "WorkflowComponent",
    "ComponentExecutable",
    "ComponentComposable",
    "WorkflowComponentMetadata",
    "ComponentConfig",
    "ComponentState",
    "ComponentAbility"
]

_LLM_RELATED_COMPONENTS = [
    "LLMComponent",
    "LLMCompConfig",
    "IntentDetectionComponent",
    "IntentDetectionCompConfig",
    "QuestionerComponent",
    "QuestionerConfig",
    "FieldInfo"
]

_FLOW_RELATED_COMPONENTS = [
    "Start",
    "End",
    "EndConfig",
    "SubWorkflowComponent",
    "BranchComponent",
    "LoopComponent",
    "LoopGroup",
    "LoopBreakComponent",
    "LoopSetVariableComponent",
    "BranchRouter",
    "Branch",
]

_TOOL_RELATED_COMPONENTS = [
    "ToolComponent",
    "ToolComponentConfig",
]

_RESOURCE_RELATED_COMPONENTS = [
    "KnowledgeRetrievalComponent",
    "KnowledgeRetrievalCompConfig",
]

_HTTP_RELATED_COMPONENTS = [
    "HTTPRequestComponent",
    "HttpComponentConfig",
    "HttpRequestParamConfig",
    "HttpAuthConfig",
    "HttpRequestBodyConfig",
    "HttpResponseHandlingConfig",
    "HttpAdvancedOptionsConfig",
    "HttpRetryConfig",
    "HttpRateLimitConfig",
    "HttpAuthType",
    "HttpContentType",
    "HttpResponseFormat"
]


_CONDITION_CLASSES = [
    "Condition",
    "FuncCondition",
    "ExpressionCondition",
    "ArrayCondition",
    "NumberCondition",
    "AlwaysTrue",
]

_SESSION = [
    "Session",
    "create_workflow_session",
]

__all__ = (
        _WORKFLOW_CLASSES +
        _COMPONENTS_CLASSES +
        _LLM_RELATED_COMPONENTS +
        _FLOW_RELATED_COMPONENTS +
        _TOOL_RELATED_COMPONENTS +
        _HTTP_RELATED_COMPONENTS +
        _RESOURCE_RELATED_COMPONENTS +
        _CONDITION_CLASSES +
        _WORKFLOW_METHODS +
        _SESSION
)
