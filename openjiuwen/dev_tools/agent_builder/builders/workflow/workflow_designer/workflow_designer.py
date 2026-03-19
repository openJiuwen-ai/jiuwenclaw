# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, List, Any, Optional, Tuple

from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm import SystemMessage, UserMessage
from openjiuwen.core.common.security.json_utils import JsonUtils

from openjiuwen.dev_tools.agent_builder.utils.utils import extract_json_from_text
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
from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.generate_flowchart_prompt import (
    GENERATE_FLOWCHART_SYSTEM_PROMPT,
    GENERATE_FLOWCHART_USER_PROMPT_TEMPLATE,
)
from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer.check_cycle_prompt import (
    CHECK_CYCLE_SYSTEM_PROMPT,
    CHECK_CYCLE_USER_PROMPT_TEMPLATE,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ApplicationError

logger = LogManager.get_logger("agent_builder")


class WorkflowDesigner:
    """SE workflow designer.

    Executes from user input: 
    basic design -> branch design -> reflection evaluation -> flowchart generation and cycle check,
    outputs final workflow design text and acyclic Mermaid flowchart.

    Example:
        ```python
        designer = WorkflowDesigner(llm_service)
        design_str, mermaid_code = designer.design("Create a customer service workflow", tool_list="")
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
        return self._parse_reflection_result(llm_response)

    @staticmethod
    def _parse_reflection_result(reflection_result: str) -> str:
        """Parse the 'New Workflow Design' section from reflection evaluation result."""
        for sep in ("## New Workflow Design", " New Workflow Design"):
            parts = reflection_result.split(sep, 1)
            if len(parts) > 1:
                return parts[1].strip()
        return reflection_result

    def generate_flowchart(
        self,
        user_input: str,
        design: str,
        loop_desc: str,
        temp: str,
    ) -> str:
        """Generate Mermaid flowchart from workflow design."""
        user_messages = GENERATE_FLOWCHART_USER_PROMPT_TEMPLATE.format({
            "user_query": user_input,
            "design": design,
            "temp": temp,
            "loop_desc": loop_desc,
        }).to_messages()
        user_prompt = user_messages[0].content
        return asyncio.run(self.llm.invoke([
            SystemMessage(content=GENERATE_FLOWCHART_SYSTEM_PROMPT),
            UserMessage(content=user_prompt),
        ])).content

    def check_mermaid_cycle(self, mermaid_code: str) -> str:
        """Check if Mermaid code contains cycles, returns JSON string."""
        user_messages = CHECK_CYCLE_USER_PROMPT_TEMPLATE.format({
            "mermaid_code": mermaid_code,
        }).to_messages()
        user_prompt = user_messages[0].content
        return asyncio.run(self.llm.invoke([
            SystemMessage(content=CHECK_CYCLE_SYSTEM_PROMPT),
            UserMessage(content=user_prompt),
        ])).content

    @staticmethod
    def _parse_cycle_result_json(inputs: str) -> Tuple[bool, str]:
        """Parse cycle check returned JSON."""
        json_str = extract_json_from_text(inputs)
        result_dict = JsonUtils.safe_json_loads(json_str)
        need_refined = result_dict.get("need_refined", False)
        loop_desc = result_dict.get("loop_desc", "")
        return bool(need_refined), str(loop_desc)

    def generate_flowchart_with_cycle_check(
        self,
        user_input: str,
        design: str,
        max_retries: int = 3,
    ) -> str:
        """Iteratively generate flowchart and check for cycles until acyclic or max retries reached."""
        attempts = 0
        need_refined = True
        temp = ""
        loop_desc = ""

        while attempts < max_retries and need_refined:
            attempts += 1
            if attempts == 1:
                loop_desc = ""
                temp = ""

            mermaid_code = self.generate_flowchart(
                user_input, design, loop_desc, temp
            )
            cycle_result_json = self.check_mermaid_cycle(mermaid_code)
            need_refined, cycle_info = self._parse_cycle_result_json(
                cycle_result_json
            )

            if need_refined:
                if attempts < max_retries:
                    temp = mermaid_code
                    loop_desc = (
                        f"Current workflow design may contain cycle structure: {cycle_info}\n"
                        "Must strictly follow DAG principle, can modify the design, "
                        "ensure workflow does not contain any closed loops or cycle structures!!!"
                    )
                else:
                    raise ApplicationError(
                        StatusCode.WORKFLOW_DL_GENERATION_ERROR,
                        msg="Max retries reached, unable to generate acyclic flowchart",
                    )
            else:
                logger.info("Successfully generated acyclic flowchart")
                return mermaid_code

        raise ApplicationError(
            StatusCode.WORKFLOW_DL_GENERATION_ERROR,
            msg="Unable to generate acyclic flowchart",
        )

    def design(
        self,
        user_input: str,
        tool_list: str,
    ) -> Tuple[str, str]:
        """
        Execute complete SE workflow design process.

        Args:
            user_input: User creation instruction (or formatted dialog history)
            tool_list: Available API/tool list string description

        Returns:
            Tuple[str, str]: (optimized workflow design text, acyclic Mermaid flowchart code)

        Raises:
            ApplicationError: When flowchart has cycles and retries exhausted
        """
        logger.info("Starting complete workflow design process (SE design)")

        logger.debug("Step 1/4: Basic design")
        basic_result = self.basic_design(user_input, tool_list)
        logger.debug("Basic design completed")

        logger.debug("Step 2/4: Branch design")
        branch_result = self.branch_design(user_input, basic_result)
        logger.debug("Branch design completed")

        logger.debug("Step 3/4: Reflection evaluation")
        reflection_result = self.reflection_evaluation(
            user_input, basic_result, branch_result
        )
        logger.debug("Reflection evaluation completed")

        logger.debug("Step 4/4: Workflow design parsing and flowchart generation")
        try:
            flowchart_result = self.generate_flowchart_with_cycle_check(
                user_input=user_input,
                design=reflection_result,
                max_retries=3,
            )
        except ApplicationError:
            raise
        except Exception as e:
            raise ApplicationError(
                StatusCode.WORKFLOW_DL_GENERATION_ERROR,
                msg=f"Flowchart generation failed: {str(e)}",
                cause=e,
            ) from e

        logger.debug("SE workflow design process completed")
        return reflection_result, flowchart_result
