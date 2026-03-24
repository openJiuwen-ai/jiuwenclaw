# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from typing import List, Dict, Any, Final

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ApplicationError
from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm import SystemMessage
from openjiuwen.core.common.security.json_utils import JsonUtils

from openjiuwen.dev_tools.agent_builder.builders.workflow.prompts import (
    INITIAL_INTENTION_SYSTEM_PROMPT,
    INITIAL_INTENTION_USER_TEMPLATE,
    REFINE_INTENTION_SYSTEM_PROMPT,
    REFINE_INTENTION_USER_TEMPLATE,
)
from openjiuwen.dev_tools.agent_builder.utils.utils import extract_json_from_text

logger = LogManager.get_logger("agent_builder")


class IntentionDetector:
    """Intention detector.

    Detects user intent, determines whether process description is provided or workflow needs refinement.

    Example:
        ```python
        detector = IntentionDetector(llm_service)
        has_instruction = detector.detect_initial_instruction(dialog_history)
        need_refine = detector.detect_refine_intent(dialog_history, mermaid_code)
        ```
    """

    ROLE: Final[str] = "role"
    CONTENT: Final[str] = "content"
    ROLE_MAP: Dict[str, str] = {
        'user': 'User',
        'assistant': 'Assistant',
        'system': 'System'
    }

    def __init__(self, llm: Model) -> None:
        """
        Initialize intention detector.

        Args:
            llm: LLM service instance
        """
        self.llm: Model = llm

    @classmethod
    def format_dialog_history(cls, dialog_history: List[Dict[str, Any]]) -> str:
        """
        Format dialog history.

        Args:
            dialog_history: Dialog history list

        Returns:
            Formatted dialog history string
        """
        formatted_lines: List[str] = []
        for msg in dialog_history:
            role = msg.get(cls.ROLE)
            content = msg.get(cls.CONTENT)
            role_display = cls.ROLE_MAP.get(role, 'User')
            formatted_lines.append(f"{role_display}: {content}")
        return "\n".join(formatted_lines)

    @staticmethod
    def extract_intent(inputs: str) -> Dict[str, Any]:
        """
        Extract intent judgment result.

        Args:
            inputs: Text returned by LLM

        Returns:
            Intent judgment result dictionary
        """
        json_str = extract_json_from_text(inputs)
        return JsonUtils.safe_json_loads(json_str)

    def detect_initial_instruction(
            self,
            messages: List[Dict[str, Any]]
    ) -> bool:
        """
        Detect whether initial process description is provided.

        Args:
            messages: Dialog history list

        Returns:
            True if process description is provided, False otherwise

        Raises:
            ApplicationError: When detection fails
        """
        try:
            if not messages:
                return False

            formatted_history = self.format_dialog_history(messages)
            system_msg = SystemMessage(content=INITIAL_INTENTION_SYSTEM_PROMPT)
            user_messages = INITIAL_INTENTION_USER_TEMPLATE.format({
                "dialog_history": formatted_history
            }).to_messages()

            model_response = asyncio.run(self.llm.invoke([system_msg] + user_messages)).content

            operation_result = self.extract_intent(model_response)
            return operation_result.get("provide_process", False)

        except Exception as e:
            logger.error("Intent detection failed", error=str(e))
            raise ApplicationError(
                StatusCode.WORKFLOW_INTENTION_DETECT_ERROR,
                msg=f"Process intent judgment exception: {str(e)}",
                cause=e,
            ) from e

    def detect_refine_intent(
            self,
            messages: List[Dict[str, Any]],
            flowchart_code: str
    ) -> bool:
        """
        Detect whether workflow needs refinement.

        Args:
            messages: Dialog history list
            flowchart_code: Current Mermaid flowchart code

        Returns:
            True if refinement is needed, False otherwise

        Raises:
            ApplicationError: When detection fails
        """
        try:
            if not messages:
                return False

            formatted_history = self.format_dialog_history(messages)
            system_msg = SystemMessage(content=REFINE_INTENTION_SYSTEM_PROMPT)
            user_messages = REFINE_INTENTION_USER_TEMPLATE.format({
                "mermaid_code": flowchart_code,
                "dialog_history": formatted_history,
            }).to_messages()

            model_response = asyncio.run(self.llm.invoke([system_msg] + user_messages)).content

            operation_result = self.extract_intent(model_response)
            return operation_result.get("need_refined", False)

        except Exception as e:
            logger.error("Refinement intent detection failed", error=str(e))
            raise ApplicationError(
                StatusCode.WORKFLOW_INTENTION_DETECT_ERROR,
                msg=f"Process intent judgment exception: {str(e)}",
                cause=e,
            ) from e
