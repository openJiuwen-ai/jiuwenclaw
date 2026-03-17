# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Intent Schema Definitions

Main classes included:
- IntentType: Intent type enumeration
- Intent: Intent data model

Intent schema defines the structure and types of user intents,
used by the controller to identify and process user requests.
"""


from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, model_validator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.controller.schema.event import Event


class IntentType(Enum):
    """Intent Type Enumeration

    Defines all intent types that users may express, used by the controller
    to identify user requests and route them to appropriate processing logic.

    Intent Type Descriptions:
    - CREATE_TASK: Create new task. Execute a new task, if there are currently
        executing tasks, they will be interrupted first.
    - PAUSE_TASK: Pause task. Pause the currently executing task, task status
        becomes paused, can be resumed later.
    - RESUME_TASK: Resume task. Resume a previously paused task, change task
        status from paused to submitted.
    - CONTINUE_TASK: Continue task. Continue executing a new task based on a
        completed task, the new task will depend on the completed task's context.
    - SUPPLEMENT_TASK: Supplement task information. Provide necessary information
        for a task that requires user input, then continue execution.
    - CANCEL_TASK: Cancel task. Cancel the currently executing task, task status
        becomes cancelled.
    - MODIFY_TASK: Modify task. Modify parameters or configuration of an executing
        task, after modification task status becomes submitted and re-executes.
    - SWITCH_TASK: Switch task. Interrupt all currently executing tasks, then execute
        a new task.
    - UNKNOWN_TASK: Unknown intent. Unrecognized user intent, requires clarification
        from the user.
    """
    CREATE_TASK = "create_task"  # Execute new task / interrupt executing tasks and execute new task
    PAUSE_TASK = "pause_task"  # Pause executing task
    RESUME_TASK = "resume_task"  # Resume task (resume previously paused task)
    CONTINUE_TASK = "continue_task"  # Continue task (continue executing task based on completed task)
    SUPPLEMENT_TASK = "supplement_task"  # Supplement necessary information for task
    CANCEL_TASK = "cancel_task"  # Cancel currently executing task
    MODIFY_TASK = "modify_task"  # Modify executing task
    SWITCH_TASK = "switch_task"  # Switch task (interrupt current task and execute another task)
    UNKNOWN_TASK = "unknown_task"  # Unknown intent, requires user clarification


class Intent(BaseModel):
    """Intent Data Model

    Represents a user's intent, containing intent type, associated event, target task, and other information.
    The intent recognizer (IntentRecognizer) will convert user input events into Intent objects,
    then route them to appropriate processing logic based on intent type.

    Attributes:
        intent_type: Intent type, identifies the type of operation the user wants to perform
        event: Associated event object, usually InputEvent, containing the user's original input
        target_task_id: Target task ID, identifies the task targeted by the intent (if applicable)
        target_task_description: Target task description, used when creating new tasks, describes
            the task to be executed
        depend_task_id: Dependent task ID, used for CONTINUE_TASK intent, identifies the task to
            continue from
        supplementary_info: Supplementary information, used for SUPPLEMENT_TASK intent, contains
            information to be supplemented
        modification_details: Modification details, used for MODIFY_TASK intent, contains content
            to be modified
        confidence: Confidence score, represents the confidence of intent recognition, range
            0.0-1.0, default is 1.0
        metadata: Metadata, can store additional intent-related information
        clarification_prompt: Clarification prompt, used for UNKNOWN_TASK intent, question to
            request clarification from user

    Note:
        - _validate() will be automatically called after creating an Intent object
    """
    intent_type: IntentType
    event: "Event"
    target_task_id: Optional[str]
    target_task_description: Optional[str] = None
    depend_task_id: List[str] = None
    supplementary_info: Optional[str] = None
    modification_details: Optional[str] = None
    confidence: float = 1.0
    metadata: Optional[Dict[str, Any]] = None
    clarification_prompt: Optional[str] = None

    @model_validator(mode='after')
    def _post_init(self):
        """Post-initialization processing

        Ensures metadata is not None and calls validation method.
        """
        if self.metadata is None:
            self.metadata = {}
        self._validate()
        return self

    def _validate(self):
        """Validate intent data

        Validates that the intent object's fields meet requirements, for example:
        - Certain intent types must contain specific fields
        - Field value validity checks

        Raises:
            ValueError: If intent data is invalid
        """
        # Validate confidence range
        if not 0.0 <= self.confidence <= 1.0:
            raise build_error(
                StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                error_msg=f"Confidence must be between 0.0 and 1.0, got {self.confidence}"
            )

        # Validate intent-specific required fields
        if self.intent_type == IntentType.CREATE_TASK:
            if not self.target_task_description:
                raise build_error(
                    StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                    error_msg="CREATE_TASK intent requires target_task_description"
                )

        elif self.intent_type == IntentType.CONTINUE_TASK:
            if not self.depend_task_id:
                raise build_error(
                    StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                    error_msg="CONTINUE_TASK intent requires depend_task_id"
                )

        elif self.intent_type == IntentType.SUPPLEMENT_TASK:
            if not self.target_task_id:
                raise build_error(
                    StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                    error_msg="SUPPLEMENT_TASK intent requires target_task_id"
                )

            if not self.supplementary_info:
                raise build_error(
                    StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                    error_msg="SUPPLEMENT_TASK intent requires supplementary_info"
                )

        elif self.intent_type == IntentType.MODIFY_TASK:
            if not self.target_task_id:
                raise build_error(
                    StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                    error_msg="MODIFY_TASK intent requires target_task_id"
                )

            if not self.modification_details:
                raise build_error(
                    StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                    error_msg="MODIFY_TASK intent requires modification_details"
                )

        elif self.intent_type in (IntentType.PAUSE_TASK, IntentType.RESUME_TASK, IntentType.CANCEL_TASK):
            if not self.target_task_id:
                raise build_error(
                    StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                    error_msg=f"{self.intent_type.value} intent requires target_task_id"
                )

        elif self.intent_type == IntentType.SWITCH_TASK:
            if not self.target_task_description:
                raise build_error(
                    StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                    error_msg="SWITCH_TASK intent requires target_task_description"
                )

        elif self.intent_type == IntentType.UNKNOWN_TASK:
            if not self.clarification_prompt:
                raise build_error(
                    StatusCode.AGENT_CONTROLLER_RUNTIME_ERROR,
                    error_msg="UNKNOWN_TASK intent requires clarification_prompt"
                )
