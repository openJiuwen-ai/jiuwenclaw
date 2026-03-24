# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from typing import Dict, List, Any, Optional, Tuple

from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm import SystemMessage, UserMessage

from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.basic_design_prompt import (
    BASIC_DESIGN_SYSTEM_PROMPT,
    BASIC_DESIGN_USER_PROMPT_TEMPLATE,
)
from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.branch_design_prompt import (
    BRANCH_DESIGN_SYSTEM_PROMPT,
    BRANCH_DESIGN_USER_PROMPT_TEMPLATE,
)
from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.reflection_evaluate_prompt import (
    REFLECTION_EVALUATE_SYSTEM_PROMPT,
    REFLECTION_EVALUATE_USER_PROMPT_TEMPLATE,
)

logger = LogManager.get_logger("agent_builder")


class WorkflowDesigner:
    """SE workflow designer.

    Executes from user input:
    basic design -> branch design -> reflection evaluation,
    outputs final workflow design text.

    Example:
        ```python
        designer = WorkflowDesigner(llm_service)
        design_str = designer.design("Create a customer service workflow", tool_list="")
        ```
    """

    def __init__(self, llm: Model) -> None:
        """
        Initialize workflow designer.

        Args:
            llm: LLM service instance
        """
        self.llm: Model = llm

    def basic_design(self, user_input: str, tool_list: str) -> str:
        """Basic design: input requirements + functional modules + implementation steps."""
        user_messages = BASIC_DESIGN_USER_PROMPT_TEMPLATE.format({
            "user_query": user_input,
            "tool_list": tool_list,
        }).to_messages()
        user_prompt = user_messages[0].content
        return asyncio.run(self.llm.invoke([
            SystemMessage(content=BASIC_DESIGN_SYSTEM_PROMPT),
            UserMessage(content=user_prompt),
        ])).content

    def branch_design(self, user_input: str, basic_result: str) -> str:
        """Branch design: identify branch points and design branch structure."""
        user_messages = BRANCH_DESIGN_USER_PROMPT_TEMPLATE.format({
            "user_query": user_input,
            "basic_design": basic_result,
        }).to_messages()
        user_prompt = user_messages[0].content
        return asyncio.run(self.llm.invoke([
            SystemMessage(content=BRANCH_DESIGN_SYSTEM_PROMPT),
            UserMessage(content=user_prompt),
        ])).content

    def reflection_evaluation(
        self,
        user_input: str,
        basic_result: str,
        branch_result: str,
    ) -> str:
        """Reflection evaluation: evaluate and output optimized complete workflow design."""
        user_messages = REFLECTION_EVALUATE_USER_PROMPT_TEMPLATE.format({
            "user_query": user_input,
            "basic_design": basic_result,
            "branch_design": branch_result,
        }).to_messages()
        user_prompt = user_messages[0].content
        llm_response = asyncio.run(self.llm.invoke([
            SystemMessage(content=REFLECTION_EVALUATE_SYSTEM_PROMPT),
            UserMessage(content=user_prompt),
        ])).content
        return self.parse_reflection_result(llm_response)

    @staticmethod
    def parse_reflection_result(reflection_result: str) -> str:
        """Parse the 'New Workflow Design' section from reflection evaluation result."""
        for sep in ("## New Workflow Design", " New Workflow Design"):
            parts = reflection_result.split(sep, 1)
            if len(parts) > 1:
                return parts[1].strip()
        return reflection_result

    def design(
        self,
        user_input: str,
        tool_list: str,
    ) -> str:
        """
        Execute complete SE workflow design process.

        Args:
            user_input: User creation instruction (or formatted dialog history)
            tool_list: Available API/tool list string description

        Returns:
            str: Optimized workflow design text
        """
        logger.info("Starting complete workflow design process (SE design)")

        logger.debug("Step 1/3: Basic design")
        basic_result = self.basic_design(user_input, tool_list)
        logger.debug("Basic design completed")

        logger.debug("Step 2/3: Branch design")
        branch_result = self.branch_design(user_input, basic_result)
        logger.debug("Branch design completed")

        logger.debug("Step 3/3: Reflection evaluation")
        reflection_result = self.reflection_evaluation(
            user_input, basic_result, branch_result
        )
        logger.debug("Reflection evaluation completed")

        logger.debug("SE workflow design process completed")
        return reflection_result
