# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import uuid
from typing import List, Dict, Tuple

from openjiuwen.core.controller import Intent, IntentType, TaskStatus
from openjiuwen.core.controller.modules.task_manager import TaskFilter


class IntentToolkits:
    def __init__(self, event, confidence_threshold: float):
        self.event = event
        self.confidence_threshold = confidence_threshold

        self._tool_schema_choices = {
            "create_task": {
                "type": "function",
                "function": {
                    "name": "create_task",
                    "description": "Create a new task. Use this method when the user "
                                   "wants to start a new task or activity.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "confidence": {
                                "type": "number",
                                "description": "Your confidence score for this operation (0-1.0), "
                                               "typically used when confidence is low"
                            },
                            "task_description": {
                                "type": "string",
                                "description": "Detailed description of the task, specifying what the "
                                               "user wants to accomplish"
                            },
                            "dependent_task_id": {
                                "type": "string",
                                "description": "Optional parameter specifying the ID of the predecessor "
                                               "task on which this task depends, used for task dependencies"
                            }
                        },
                        "required": ["confidence", "task_description"],
                        "additionalProperties": False
                    }
                }
            },
            "pause_task": {
                "type": "function",
                "function": {
                    "name": "pause_task",
                    "description": "Pause a specific task. Use when the user wants to temporarily "
                                   "interrupt or suspend an ongoing task.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "confidence": {
                                "type": "number",
                                "description": "Your confidence score for this operation (0-1.0), "
                                               "typically used when confidence is low"
                            },
                            "task_id": {
                                "type": "string",
                                "description": "Unique identifier of the task to be paused"
                            }
                        },
                        "required": ["confidence", "task_id"],
                        "additionalProperties": False
                    }
                }
            },
            "cancel_task": {
                "type": "function",
                "function": {
                    "name": "cancel_task",
                    "description": "Cancel a specific task. Use when the user wants to completely"
                                   " terminate or abandon a task.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "confidence": {
                                "type": "number",
                                "description": "Your confidence score for this operation (0-1.0), "
                                               "typically used when confidence is low"
                            },
                            "task_id": {
                                "type": "string",
                                "description": "Unique identifier of the task to be canceled"
                            }
                        },
                        "required": ["confidence", "task_id"],
                        "additionalProperties": False
                    }
                }
            },
            "resume_task": {
                "type": "function",
                "function": {
                    "name": "resume_task",
                    "description": "Resume a specific task. Use when the user wants to continue a "
                                   "previously paused or interrupted task.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "confidence": {
                                "type": "number",
                                "description": "Your confidence score for this operation (0-1.0), "
                                               "typically used when confidence is low"
                            },
                            "task_id": {
                                "type": "string",
                                "description": "Unique identifier of the task to be resumed"
                            }
                        },
                        "required": ["confidence", "task_id"],
                        "additionalProperties": False
                    }
                }
            },
            "unknown_task": {
                "type": "function",
                "function": {
                    "name": "unknown_task",
                    "description": "Handle unknown or ambiguous user intents. Use this method when the "
                                   "exact user intent cannot be determined to create clarification "
                                   "questions for the user.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "confidence": {
                                "type": "number",
                                "description": "Your confidence score for this operation (0-1.0), "
                                               "typically used when confidence is low"
                            },
                            "question_for_user": {
                                "type": "string",
                                "description": "Clarification question to ask the user to obtain "
                                               "more information to determine the exact intent"
                            }
                        },
                        "required": ["confidence", "question_for_user"],
                        "additionalProperties": False
                    }
                }
            },
            "create_dependent_task": {
                "type": "function",
                "function": {
                    "name": "create_dependent_task",
                    "description": "Create a new task that depends on one or more existing tasks. "
                                   "Use when the user wants to start a task that requires completion "
                                   "of other tasks first.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "confidence": {
                                "type": "number",
                                "description": "Your confidence score for this operation (0-1.0), "
                                               "typically used when confidence is low"
                            },
                            "task_description": {
                                "type": "string",
                                "description": "Detailed description of the dependent task"
                            },
                            "dependent_task_ids": {
                                "type": "array",
                                "items": {
                                    "type": "string"
                                },
                                "description": "List of task IDs that this task depends on"
                            }
                        },
                        "required": ["confidence", "task_description", "dependent_task_ids"],
                        "additionalProperties": False
                    }
                }
            },
            "modify_task": {
                "type": "function",
                "function": {
                    "name": "modify_task",
                    "description": "Modify an existing task by creating a new version with "
                                   "updated description. Use when the user wants to change "
                                   "the details or requirements of an existing task.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "confidence": {
                                "type": "number",
                                "description": "Your confidence score for this operation (0-1.0), "
                                               "typically used when confidence is low"
                            },
                            "task_id": {
                                "type": "string",
                                "description": "Unique identifier of the task to be modified"
                            },
                            "new_task_description": {
                                "type": "string",
                                "description": "Updated description for the task"
                            }
                        },
                        "required": ["confidence", "task_id", "new_task_description"],
                        "additionalProperties": False
                    }
                }
            },
            "supplement_task": {
                "type": "function",
                "function": {
                    "name": "supplement_task",
                    "description": "Add supplementary information to an existing task. Use when "
                                   "the user wants to provide additional details or context for "
                                   "an ongoing task without changing its core description.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "confidence": {
                                "type": "number",
                                "description": "Your confidence score for this operation (0-1.0), "
                                               "typically used when confidence is low"
                            },
                            "task_id": {
                                "type": "string",
                                "description": "Unique identifier of the task to be supplemented"
                            },
                            "supplement_info": {
                                "type": "string",
                                "description": "Additional information or context to add to the task"
                            }
                        },
                        "required": ["confidence", "task_id", "supplement_info"],
                        "additionalProperties": False
                    }
                }
            }
        }

    def _low_confidence_intent(self, confidence: float) -> Tuple[Intent, str]:
        return Intent(
            intent_type=IntentType.UNKNOWN_TASK,
            event=self.event,
            target_task_id="",
            target_task_description=None,
            depend_task_id=[],
            supplementary_info=None,
            modification_details=None,
            confidence=confidence,
            clarification_prompt="Sorry, I couldn't understand your meaning. "
                                 "Please clarify whether you want to create a new "
                                 "task or modify an existing one.",
        ), f"Automatically converted to unknown_task due to low confidence"

    async def create_task(self, confidence: float, task_description: str) -> Tuple[Intent, str]:
        if confidence < self.confidence_threshold:
            return self._low_confidence_intent(confidence)
        target_task_id = str(uuid.uuid4())
        return Intent(
            intent_type=IntentType.CREATE_TASK,
            event=self.event,
            target_task_id=target_task_id,
            target_task_description=task_description,
            depend_task_id=[],
            supplementary_info=None,
            modification_details=None,
            confidence=confidence,
            clarification_prompt=None
        ), (f"Task ID: {target_task_id}, Task Description: {task_description}, "
            f"Current Status: Created and submitted for execution")

    async def pause_task(self, confidence: float, task_id: str) -> Tuple[Intent, str]:
        if confidence < self.confidence_threshold:
            return self._low_confidence_intent(confidence)
        return Intent(
            intent_type=IntentType.PAUSE_TASK,
            event=self.event,
            target_task_id=task_id,
            target_task_description=None,
            depend_task_id=[],
            supplementary_info=None,
            modification_details=None,
            confidence=confidence,
            clarification_prompt=None
        ), f"Task ID: {task_id}, Current Status: Paused"

    async def cancel_task(self, confidence: float, task_id: str) -> Tuple[Intent, str]:
        if confidence < self.confidence_threshold:
            return self._low_confidence_intent(confidence)
        return Intent(
            intent_type=IntentType.CANCEL_TASK,
            event=self.event,
            target_task_id=task_id,
            target_task_description=None,
            depend_task_id=[],
            supplementary_info=None,
            modification_details=None,
            confidence=confidence,
            clarification_prompt=None
        ), f"Task ID: {task_id}, Current Status: Canceled"

    async def resume_task(self, confidence: float, task_id: str) -> Tuple[Intent, str]:
        if confidence < self.confidence_threshold:
            return self._low_confidence_intent(confidence)
        return Intent(
            intent_type=IntentType.RESUME_TASK,
            event=self.event,
            target_task_id=task_id,
            target_task_description=None,
            depend_task_id=[],
            supplementary_info=None,
            modification_details=None,
            confidence=confidence,
            clarification_prompt=None
        ), f"Task ID: {task_id}, Current Status: Resumed"

    async def unknown_task(self, confidence: float, question_for_user: str) -> Tuple[Intent, str]:
        if confidence < self.confidence_threshold:
            return self._low_confidence_intent(confidence)
        return Intent(
            intent_type=IntentType.UNKNOWN_TASK,
            event=self.event,
            target_task_id="",
            target_task_description=None,
            depend_task_id=[],
            supplementary_info=None,
            modification_details=None,
            confidence=confidence,
            clarification_prompt=question_for_user,
        ), f"Request sent, waiting for user response."

    async def create_dependent_task(
            self,
            confidence: float,
            task_description: str,
            dependent_task_ids: List[str]
    ) -> Tuple[Intent, str]:
        if confidence < self.confidence_threshold:
            return self._low_confidence_intent(confidence)
        target_task_id = str(uuid.uuid4())
        return Intent(
            intent_type=IntentType.CONTINUE_TASK,
            event=self.event,
            target_task_id=target_task_id,
            target_task_description=task_description,
            depend_task_id=dependent_task_ids,
            supplementary_info=None,
            modification_details=None,
            confidence=confidence,
            clarification_prompt=None,
        ), (f"Task ID: {target_task_id}, Task Description: {task_description}, "
            f"Current Status: Created and submitted for execution")

    async def modify_task(self, confidence: float, task_id: str, new_task_description: str) -> Tuple[Intent, str]:
        if confidence < self.confidence_threshold:
            return self._low_confidence_intent(confidence)
        target_task_id = str(uuid.uuid4())
        return Intent(
            intent_type=IntentType.MODIFY_TASK,
            event=self.event,
            target_task_id=target_task_id,
            target_task_description=new_task_description,
            depend_task_id=[task_id],
            supplementary_info=None,
            modification_details=new_task_description,
            confidence=confidence,
            clarification_prompt=None,
        ), (f"Task ID: {target_task_id}, Task Description: {new_task_description}, "
            f"Current Status: Created and submitted for execution")

    async def supplement_task(self, confidence: float, task_id: str, supplement_info: str) -> Tuple[Intent, str]:
        if confidence < self.confidence_threshold:
            return self._low_confidence_intent(confidence)
        return Intent(
            intent_type=IntentType.SUPPLEMENT_TASK,
            event=self.event,
            target_task_id=task_id,
            target_task_description=None,
            depend_task_id=[],
            supplementary_info=supplement_info,
            modification_details=None,
            confidence=confidence,
            clarification_prompt=None,
        ), f"Task supplementary information submitted."

    def get_openai_tool_schemas(self, choices: List[str] = None) -> List[Dict]:
        """
        Get OpenAI Tool Schemas

        Returns:
            List[Dict]: OpenAI tool schemas
        """
        if not choices:
            return list(self._tool_schema_choices.values())
        return [self._tool_schema_choices[k] for k in self._tool_schema_choices.keys()]