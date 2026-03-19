# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Dict, Any, Optional, Union

from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.foundation.llm import Model

from openjiuwen.dev_tools.agent_builder.builders.base import BaseAgentBuilder
from openjiuwen.dev_tools.agent_builder.builders.llm_agent.clarifier import Clarifier
from openjiuwen.dev_tools.agent_builder.builders.llm_agent.generator import Generator
from openjiuwen.dev_tools.agent_builder.builders.llm_agent.transformer import Transformer
from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager
from openjiuwen.dev_tools.agent_builder.utils.enums import BuildState, ProgressStage
from openjiuwen.dev_tools.agent_builder.utils.utils import format_dialog_history

logger = LogManager.get_logger("agent_builder")


class LlmAgentBuilder(BaseAgentBuilder):
    """LLM Agent Builder

    Implements LLM Agent building logic, including:
    1. Requirement clarification (INITIAL state)
    2. Config generation and DSL transformation (PROCESSING state)

    Example:
        ```python
        builder = LlmAgentBuilder(llm_service, history_manager)
        result = builder.execute("Create a customer service assistant")
        ```
    """

    def __init__(
            self,
            llm: Model,
            history_manager: HistoryManager
    ) -> None:
        """
        Initialize LLM Agent Builder

        Args:
            llm: LLM service instance
            history_manager: Context manager instance
        """
        super().__init__(llm, history_manager)
        self._agent_config_info: Optional[str] = None
        self._clarifier: Clarifier = Clarifier(llm)
        self._generator: Generator = Generator(llm)
        self._transformer: Transformer = Transformer()

    def _handle_initial(
            self,
            query: str,
            dialog_history: List[Dict[str, str]]
    ) -> str:
        """
        Handle initial state: clarify requirements

        Args:
            query: User query
            dialog_history: Dialog history

        Returns:
            Clarification result string
        """
        logger.info("Starting requirement clarification", query_length=len(query))

        if self._progress_reporter:
            self._progress_reporter.start_stage(
                ProgressStage.CLARIFYING,
                "Analyzing requirements and clarifying questions...",
                {"query_length": len(query)}
            )

        messages_str = format_dialog_history(dialog_history)

        factor_output, display_resource, resource_id_dict = (
            self._clarifier.clarify(
                messages_str,
                resource=self._resource
            )
        )

        if self._progress_reporter:
            self._progress_reporter.update_stage(
                "Requirement clarification completed, organizing information...",
                {"has_resources": bool(display_resource)}
            )

        response = factor_output
        if display_resource:
            response += "\n\n" + display_resource

        self.history_manager.add_assistant_message(response)
        self._agent_config_info = factor_output

        for key, value in resource_id_dict.items():
            if key in self._resource:
                if isinstance(self._resource[key], list):
                    self._resource[key].extend(value)
                else:
                    self._resource[key] = value
            else:
                self._resource[key] = value

        if self._progress_reporter:
            self._progress_reporter.complete_stage(
                "Requirement clarification completed",
                {"resource_count": len(resource_id_dict)}
            )

        self.state = BuildState.PROCESSING
        return response

    def _handle_processing(
            self,
            query: str,
            dialog_history: List[Dict[str, str]]
    ) -> str:
        """
        Handle processing state: generate final DSL

        Args:
            query: User query
            dialog_history: Dialog history

        Returns:
            Generated DSL string (JSON format)
        """
        logger.info("Starting DSL generation")

        if self._progress_reporter:
            self._progress_reporter.start_stage(
                ProgressStage.GENERATING_CONFIG,
                "Generating agent configuration...",
                {"has_config_info": bool(self._agent_config_info)}
            )

        messages_str = format_dialog_history(dialog_history)

        constructor_output = self._generator.generate(
            message=messages_str,
            agent_config_info=self._agent_config_info or "",
            agent_resource_info=str(self._resource),
            resource_id_dict=self._resource
        )

        if self._progress_reporter:
            self._progress_reporter.complete_stage("Configuration generation completed")
            self._progress_reporter.start_stage(
                ProgressStage.TRANSFORMING_DSL,
                "Converting to DSL format...",
                {"output_length": len(str(constructor_output))}
            )

        dsl = self._transformer.transform_to_dsl(
            constructor_output,
            resource=self._resource
        )

        if self._progress_reporter:
            self._progress_reporter.complete_stage("DSL transformation completed")

        self.reset()
        return dsl

    def _handle_completed(
            self,
            query: str,
            dialog_history: List[Dict[str, str]]
    ) -> str:
        """
        Handle completed state

        LLM Agent completes in PROCESSING state, should not enter COMPLETED state.
        If entered, state is abnormal, re-execute processing logic.

        Args:
            query: User query
            dialog_history: Dialog history

        Returns:
            Processing result
        """
        logger.warning(
            "LLM Agent should not enter COMPLETED state, re-executing processing logic"
        )
        return self._handle_processing(query, dialog_history)

    def _reset_internal_state(self) -> None:
        """Reset internal state"""
        self._agent_config_info = None
        logger.debug("LLM Agent builder internal state reset")

    def _is_workflow_builder(self) -> bool:
        """
        Determine if workflow builder

        Returns:
            False (LLM Agent builder)
        """
        return False
