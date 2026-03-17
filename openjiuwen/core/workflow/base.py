# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from enum import Enum
from typing import Any, Optional, Type, Union

from pydantic import BaseModel

from openjiuwen.core.common import BaseCard
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.session.stream import OutputSchema, CustomSchema, TraceSchema

WORKFLOW_DRAWABLE = "WORKFLOW_DRAWABLE"


class WorkflowCard(BaseCard):
    """
    Metadata card for a workflow.

    Contains descriptive information and input schema for a workflow.
    """
    version: str = ''
    input_params: Optional[dict[str, Any] | Type[BaseModel]] = None

    def tool_info(self):
        return ToolInfo(
            name=self.name,
            description=self.description,
            parameters=self.input_params if self.input_params else {}
        )


class WorkflowChunkType(str, Enum):
    """
    Types of data chunks produced during workflow execution.

    Used to categorize different kinds of output streams.
    """
    INTERACTION = "interaction"  # Stream from user/agent interactions
    OUTPUT = "output"  # Stream from final output node
    ERROR = "error"  # Stream containing error information


class WorkflowExecutionState(str, Enum):
    """
    Possible states of workflow execution.

    Indicates the current status or completion state of a workflow run.
    """
    COMPLETED = "COMPLETED"  # Workflow completed successfully
    INPUT_REQUIRED = "INPUT_REQUIRED"  # Workflow is waiting for user input
    ERROR = "ERROR"  # Workflow encountered an error


# Type alias for workflow output chunks
WorkflowChunk = Union[OutputSchema, CustomSchema, TraceSchema]


class WorkflowOutput(BaseModel):
    """
    Final output container for workflow execution.

    Contains both the result data and the execution state.
    """
    result: Any  # Output data, either as list of chunks or dictionary
    state: WorkflowExecutionState  # Final state of the workflow execution


def generate_workflow_key(workflow_id: str, workflow_version: str) -> str:
    return f"{workflow_id}_{workflow_version}"
