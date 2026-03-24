# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Dict, Any, Optional, Union, Callable

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ApplicationError
from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage

from openjiuwen.dev_tools.agent_builder.builders.base import BaseAgentBuilder
from openjiuwen.dev_tools.agent_builder.builders.workflow.intention_detector import IntentionDetector
from openjiuwen.dev_tools.agent_builder.builders.workflow.workflow_designer import WorkflowDesigner
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_generator import DLGenerator
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_reflector import Reflector
from openjiuwen.dev_tools.agent_builder.builders.workflow.dl_transformer import DLTransformer
from openjiuwen.dev_tools.agent_builder.builders.workflow.cycle_checker import CycleChecker
from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager
from openjiuwen.dev_tools.agent_builder.utils.enums import BuildState, ProgressStage
from openjiuwen.dev_tools.agent_builder.utils.constants import (
    WORKFLOW_REQUEST_CONTENT,
    WORKFLOW_DESIGN_RESPONSE_CONTENT,
    GENERATE_DL_FROM_DESIGN_CONTENT,
    MODIFY_DL_CONTENT,
    DEFAULT_MAX_RETRIES
)
from openjiuwen.dev_tools.agent_builder.utils.utils import extract_json_from_text

logger = LogManager.get_logger("agent_builder")


class WorkflowBuilder(BaseAgentBuilder):
    """Workflow builder implementing build logic.

    Includes:
    1. Intent detection and SE workflow design (INITIAL state)
    2. DL generation and optimization (PROCESSING state)
    3. DSL transformation (COMPLETED state)

    Example:
        ```python
        builder = WorkflowBuilder(llm_service, history_manager)
        result = builder.execute("Create a data processing workflow")
        ```
    """

    def __init__(
            self,
            llm: Model,
            history_manager: HistoryManager
    ) -> None:
        """Initialize Workflow builder.

        Args:
            llm: LLM service instance
            history_manager: History manager instance
        """
        super().__init__(llm, history_manager)
        self._workflow_name: Optional[str] = None
        self._workflow_name_en: Optional[str] = None
        self._workflow_desc: Optional[str] = None
        self._dl: Optional[str] = None
        self._mermaid_code: Optional[str] = None

        self._intention_detector: IntentionDetector = IntentionDetector(llm)
        self._workflow_designer: WorkflowDesigner = WorkflowDesigner(llm)
        self._dl_generator: DLGenerator = DLGenerator(llm)
        self._dl_reflector: Reflector = Reflector()
        self._dl_transformer: DLTransformer = DLTransformer()
        self._cycle_checker: CycleChecker = CycleChecker(llm)

    @property
    def workflow_name(self) -> Optional[str]:
        return self._workflow_name

    @property
    def workflow_name_en(self) -> Optional[str]:
        return self._workflow_name_en

    @property
    def workflow_desc(self) -> Optional[str]:
        return self._workflow_desc

    @property
    def dl(self) -> Optional[str]:
        return self._dl

    @property
    def mermaid_code(self) -> Optional[str]:
        return self._mermaid_code

    def _handle_initial(
            self,
            query: str,
            dialog_history: List[Dict[str, str]]
    ) -> str:
        """Handle initial state: detect intent, perform SE workflow design and generate flowchart.

        Args:
            query: User query
            dialog_history: Dialog history

        Returns:
            Processing result (request content or Mermaid code)
        """
        if self._progress_reporter:
            self._progress_reporter.start_stage(
                ProgressStage.DETECTING_INTENTION,
                "Detecting user intent...",
                {"query_length": len(query)}
            )

        if not self._intention_detector.detect_initial_instruction(dialog_history):
            if self._progress_reporter:
                self._progress_reporter.complete_stage("More information needed")
            self.history_manager.add_assistant_message(WORKFLOW_REQUEST_CONTENT)
            self.state = BuildState.PROCESSING
            return WORKFLOW_REQUEST_CONTENT

        if self._progress_reporter:
            self._progress_reporter.complete_stage("Intent detection completed")
            self._progress_reporter.start_stage(
                ProgressStage.GENERATING_WORKFLOW_DESIGN,
                "Designing workflow...",
                {"has_query": bool(query)}
            )

        tool_list = self._format_tool_list()
        design_str = self._workflow_designer.design(query, tool_list)

        design_info: Dict[str, Any] = {
            "name": query[:100] if query else "Workflow",
            "name_en": "workflow",
            "description": (design_str[:300] if design_str else "Workflow design"),
        }
        self._update_workflow_info(design_info)
        self.history_manager.add_assistant_message(
            WORKFLOW_DESIGN_RESPONSE_CONTENT + (design_str or "")
        )

        if self._progress_reporter:
            self._progress_reporter.complete_stage(
                "工作流设计完成",
                {"design_length": len(design_str or "")}
            )

        self._dl = self._generate_and_reflect_dl(
            dl_operation=self._dl_generator.generate,
            query=GENERATE_DL_FROM_DESIGN_CONTENT + (design_str or ""),
            resource=self._resource
        )

        self._mermaid_code = self._generate_mermaid_with_cycle_check(self._dl)

        self.state = BuildState.PROCESSING
        return self._mermaid_code

    def _handle_processing(
            self,
            query: str,
            dialog_history: List[Dict[str, str]]
    ) -> str:
        """Handle processing state: generate or optimize workflow.

        Args:
            query: User query
            dialog_history: Dialog history

        Returns:
            Processing result (Mermaid code or DSL)
        """
        if self._dl is None:
            if self._intention_detector.detect_initial_instruction(dialog_history):
                user_input = query
            else:
                user_input = "\n".join(
                    f"{m.get('role', 'user')}: {m.get('content', '')}"
                    for m in dialog_history
                )
            tool_list = self._format_tool_list()
            design_str = self._workflow_designer.design(user_input, tool_list)
            design_info = {
                "name": user_input[:100] if user_input else "Workflow",
                "name_en": "workflow",
                "description": (design_str[:300] if design_str else "Workflow design"),
            }
            self._update_workflow_info(design_info)
            self.history_manager.add_assistant_message(
                WORKFLOW_DESIGN_RESPONSE_CONTENT + (design_str or "")
            )

            self._dl = self._generate_and_reflect_dl(
                dl_operation=self._dl_generator.generate,
                query=GENERATE_DL_FROM_DESIGN_CONTENT + (design_str or ""),
                resource=self._resource
            )
            self._mermaid_code = self._generate_mermaid_with_cycle_check(self._dl)
            return self._mermaid_code
        else:
            if self._intention_detector.detect_refine_intent(
                    dialog_history,
                    self._mermaid_code or ""
            ):
                self._dl = self._generate_and_reflect_dl(
                    dl_operation=self._dl_generator.refine,
                    query=query,
                    resource=self._resource,
                    exist_dl=self._dl,
                    exist_mermaid=self._mermaid_code or ""
                )
                self._mermaid_code = self._generate_mermaid_with_cycle_check(self._dl)
                return self._mermaid_code
            else:
                if self._progress_reporter:
                    self._progress_reporter.start_stage(
                        ProgressStage.TRANSFORMING_WORKFLOW_DSL,
                        "Converting to workflow DSL..."
                    )

                dsl = self._dl_transformer.transform_to_dsl(
                    self._dl,
                    self._resource
                )

                if self._progress_reporter:
                    self._progress_reporter.complete_stage("Workflow DSL conversion completed")

                self.reset()
                return dsl

    def _handle_completed(
            self,
            query: str,
            dialog_history: List[Dict[str, str]]
    ) -> str:
        """Handle completed state.

        After workflow build completes, user can re-enter processing state for optimization.

        Args:
            query: User query
            dialog_history: Dialog history

        Returns:
            Processing result
        """
        if self._intention_detector.detect_refine_intent(
                dialog_history,
                self._mermaid_code or ""
        ):
            self.state = BuildState.PROCESSING
            return self._handle_processing(query, dialog_history)

        if self._dl:
            return self._dl_transformer.transform_to_dsl(
                self._dl,
                self._resource
            )

        return "Workflow build completed"

    def _generate_and_reflect_dl(
            self,
            dl_operation: Callable[..., str],
            max_retries: int = DEFAULT_MAX_RETRIES,
            *args: Any,
            **kwargs: Any
    ) -> str:
        """Generate and validate DL with retry mechanism.

        Args:
            dl_operation: DL generation function
            max_retries: Maximum retry count
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Generated DL string

        Raises:
            ApplicationError: When generation fails
        """
        max_retries = max_retries or DEFAULT_MAX_RETRIES

        logger.debug(
            "Starting DL generation",
            max_retries=max_retries,
            operation_name=dl_operation.__name__ if hasattr(dl_operation, '__name__') else 'unknown'
        )

        if self._progress_reporter:
            self._progress_reporter.start_stage(
                ProgressStage.GENERATING_DL,
                "Generating process definition language (DL)...",
                {"max_retries": max_retries}
            )

        for attempt in range(max_retries):
            try:
                if self._progress_reporter and attempt > 0:
                    self._progress_reporter.update_stage(
                        f"Retrying DL generation (attempt {attempt + 1})...",
                        {"attempt": attempt + 1, "max_retries": max_retries}
                    )

                generated_dl = dl_operation(*args, **kwargs)
                generated_dl = extract_json_from_text(generated_dl)

                logger.debug(
                    "DL generation completed",
                    attempt=attempt + 1,
                    dl_length=len(generated_dl) if generated_dl else 0,
                    dl_preview=(generated_dl[:200] if generated_dl else "None")
                )

                if self._progress_reporter:
                    self._progress_reporter.start_stage(
                        ProgressStage.VALIDATING_DL,
                        "Validating DL format...",
                        {"attempt": attempt + 1}
                    )

                self._dl_reflector.check_format(generated_dl)

                logger.debug(
                    "DL format check completed",
                    attempt=attempt + 1,
                    error_count=len(self._dl_reflector.errors),
                    errors=self._dl_reflector.errors
                )

                if not self._dl_reflector.errors:
                    self.history_manager.add_assistant_message(generated_dl)
                    logger.info(
                        "DL generation succeeded",
                        attempt=attempt + 1,
                        max_retries=max_retries
                    )

                    if self._progress_reporter:
                        self._progress_reporter.complete_stage(
                            "DL generation and validation succeeded",
                            {"attempt": attempt + 1}
                        )

                    return generated_dl

                error_messages = ";\n".join(self._dl_reflector.errors)
                logger.warning(
                    "DL format validation failed, preparing retry",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    errors=error_messages
                )

                if self._progress_reporter:
                    self._progress_reporter.warn_stage(
                        f"Format validation failed: {error_messages}",
                        f"Optimizing DL (retry {attempt + 1})...",
                        {"errors": self._dl_reflector.errors, "attempt": attempt + 1}
                    )
                    self._progress_reporter.start_stage(
                        ProgressStage.REFINING_DL,
                        f"Optimizing DL (retry {attempt + 1})...",
                        {"errors": self._dl_reflector.errors}
                    )

                self._dl_generator.reflect_prompts = [
                    AssistantMessage(content=generated_dl),
                    UserMessage(
                        content=MODIFY_DL_CONTENT + error_messages
                    ),
                ]
                if attempt < max_retries - 1:
                    self._dl_reflector.reset()

            except Exception as e:
                logger.error(
                    "Error during DL generation",
                    attempt=attempt + 1,
                    error=str(e)
                )
                if attempt == max_retries - 1:
                    if self._progress_reporter:
                        self._progress_reporter.fail_stage(
                            str(e),
                            "DL generation failed"
                        )
                    raise

        error_messages = ";\n".join(self._dl_reflector.errors)
        logger.error(
            "DL generation failed, max retries reached",
            max_retries=max_retries,
            errors=error_messages
        )

        if self._progress_reporter:
            self._progress_reporter.fail_stage(
                error_messages,
                "DL generation failed, max retries reached"
            )

        raise ApplicationError(
            StatusCode.WORKFLOW_DL_GENERATION_ERROR,
            msg=f"Process definition language (DL) generation failed, errors: {error_messages}",
        )

    def _generate_mermaid_with_cycle_check(self, dl_content: str, max_retries: int = 3) -> str:
        attempts = 0
        need_refined = True
        loop_desc = ""
        temp_dl = dl_content

        while attempts < max_retries and need_refined:
            attempts += 1

            if self._progress_reporter:
                self._progress_reporter.start_stage(
                    ProgressStage.TRANSFORMING_MERMAID,
                    f"正在从 DL 转换生成流程图（第 {attempts} 次）..."
                )

            mermaid_code = self._dl_transformer.transform_to_mermaid(temp_dl)

            need_refined, cycle_info = self._cycle_checker.check_and_parse(mermaid_code)

            if need_refined:
                if attempts < max_retries:
                    loop_desc = (
                        f"当前工作流设计方案可能包含环的结构：{cycle_info}\n"
                        "需严格遵循有向无环图(DAG)原则，可对设计方案进行改造，"
                        "确保工作流不包含任何闭环或循环结构！！！"
                    )

                    if self._progress_reporter:
                        self._progress_reporter.warn_stage(
                            f"检测到环结构：{cycle_info}",
                            f"正在优化 DL（第 {attempts + 1} 次尝试）..."
                        )

                    temp_dl = self._generate_and_reflect_dl(
                        dl_operation=self._dl_generator.refine,
                        query=f"请修复以下流程图中的环结构：{loop_desc}",
                        resource=self._resource,
                        exist_dl=temp_dl,
                        exist_mermaid=mermaid_code
                    )
                else:
                    raise ApplicationError(
                        StatusCode.WORKFLOW_EXECUTE_INPUT_INVALID,
                        msg="已达到最大重试次数，无法生成无环流程图",
                        details={
                            "max_retries": max_retries,
                            "error_code": StatusCode.WORKFLOW_DL_GENERATION_ERROR.code,
                        },
                    )
            else:
                logger.info("成功生成无环流程图")
                if self._progress_reporter:
                    self._progress_reporter.complete_stage("流程图生成完成")
                return mermaid_code

        raise ApplicationError(
            StatusCode.WORKFLOW_EXECUTE_INPUT_INVALID,
            msg="无法生成无环流程图",
            details={
                "max_retries": max_retries,
                "error_code": StatusCode.WORKFLOW_DL_GENERATION_ERROR.code,
            },
        )

    def _reset_internal_state(self) -> None:
        """Reset internal state."""
        self._workflow_name = None
        self._workflow_name_en = None
        self._workflow_desc = None
        self._dl = None
        self._mermaid_code = None
        self._dl_generator.reflect_prompts = []
        logger.debug("Workflow builder internal state reset")

    def _format_tool_list(self) -> str:
        """Format plugins from current resource as tool list string for workflow designer.

        Returns:
            Tool list string, empty string if no plugins available
        """
        plugins = (self._resource or {}).get("plugins", [])
        if not plugins:
            return ""
        return "\n".join(str(p) for p in plugins)

    def _update_workflow_info(self, design_info: Dict[str, Any]) -> None:
        """Update workflow info.

        Args:
            design_info: Workflow design info dict (contains name, name_en, description)
        """
        self._workflow_name = design_info.get("name")
        self._workflow_name_en = design_info.get("name_en")
        self._workflow_desc = design_info.get("description")

    def _is_workflow_builder(self) -> bool:
        """Check if this is a workflow builder.

        Returns:
            True (Workflow builder)
        """
        return True
