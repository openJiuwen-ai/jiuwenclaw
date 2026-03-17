# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Controller Output Data Model Definitions

This module defines the data models for controller output, including:
- ControllerOutputPayload: Controller output payload
- ControllerOutputChunk: Controller output chunk (for streaming output)
- ControllerOutput: Controller output (for batch processing output)

Output Types:
- task_completion: Task completion
- task_interaction: Task interaction (requires user input)
- task_failed: Task failed
- processing: Processing
"""

from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field

from openjiuwen.core.controller.schema.dataframe import DataFrame
from openjiuwen.core.controller.schema.event import EventType
from openjiuwen.core.session.stream.base import OutputSchema

TASK_PROCESSING = "processing"
ALL_TASKS_PROCESSED = "all_tasks_processed"


class ControllerOutputPayload(BaseModel):
    """Controller Output Payload

    Contains the output type, data, and metadata information.
    This is the core data part of controller output.

    Attributes:
        type: Output type, can be task completion, task interaction, task failed, processing or all tasks processed
        data: Output data list, contains the actual output content
        metadata: Metadata, can contain additional output information
    """
    type: Literal[EventType.TASK_COMPLETION, EventType.TASK_INTERACTION, EventType.TASK_FAILED,
                    TASK_PROCESSING, ALL_TASKS_PROCESSED]
    data: List[DataFrame] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None


class ControllerOutputChunk(OutputSchema):
    """Controller Output Chunk

    A single data chunk in streaming output, containing index, type, payload,
    and a flag indicating whether it's the last chunk. Used for streaming output
    scenarios, supporting incremental return of processing results.

    Attributes:
        index: Output chunk index, used to identify the order of output chunks
        type: Output type, fixed as "controller_output"
        payload: Output payload, contains the actual output data
        last_chunk: Whether this is the last chunk, used to indicate if streaming output has ended
    """
    index: int
    type: str = "controller_output"
    payload: ControllerOutputPayload = None
    last_chunk: bool = False


class ControllerOutput(BaseModel):
    """Controller Output

    Batch processing output result, containing type, data list, and input event ID.
    Used for non-streaming output scenarios, returning all results at once.

    Attributes:
        type: Output type, can be task completion, task interaction, task failed, or processing
        data: Output data, can be a list of ControllerOutputChunk or a dictionary
        input_event_id: Associated input event ID, used to track input-output relationships
    """
    type: Literal[EventType.TASK_COMPLETION, EventType.TASK_INTERACTION, EventType.TASK_FAILED, TASK_PROCESSING]
    data: List[ControllerOutputChunk] | Dict
    input_event_id: Optional[str] = None
