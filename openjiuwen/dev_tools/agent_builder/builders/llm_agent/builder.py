# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Dict, Any, Optional, Union

from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.foundation.llm import Model

from openjiuwen.dev_tools.agent_builder.builders.base import BaseAgentBuilder
from openjiuwen.dev_tools.agent_builder.builders.llm_agent.clarifier import Clarifier
from openjiuwen.dev_tools.agent_builder.builders.llm_agent.generator import Generator
from openjiuwen.dev_tools.agent_builder.builders.llm_agent.transformer import Transformer
from openjiuwen.dev_tools.agent_builder.builders.llm_agent.intention_detector import IntentionDetector
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
        result = builder.execute("创建一个客服助手")
        ```
    """

    RESOURCE_UNIQUE_KEY = {"plugins": "tool_id"}

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
        self._factor_output_info: Optional[str] = None
        self._display_resource_info: Optional[str] = None
        self._resource_id_dict_info: Optional[Dict[str, List[str]]] = None
        self._clarifier: Clarifier = Clarifier(llm)
        self._generator: Generator = Generator(llm)
        self._transformer: Transformer = Transformer()
        self._intention_detector: IntentionDetector = IntentionDetector(llm)

    @property
    def agent_config_info(self) -> Optional[str]:
        return self._agent_config_info

    @property
    def factor_output_info(self) -> Optional[str]:
        return self._factor_output_info

    @property
    def display_resource_info(self) -> Optional[str]:
        return self._display_resource_info

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
        logger.info("开始澄清需求", query_length=len(query))

        if self._progress_reporter:
            self._progress_reporter.start_stage(
                ProgressStage.CLARIFYING,
                "正在分析需求并澄清问题...",
                {"query_length": len(query)}
            )

        messages_str = format_dialog_history(dialog_history)

        self._factor_output_info, self._display_resource_info, self._resource_id_dict_info = (
            self._clarifier.clarify(
                messages_str,
                resource=self._resource
            )
        )
        if self._progress_reporter:
            self._progress_reporter.update_stage(
                "需求澄清完成，正在整理信息...",
                {"has_resources": bool(self._display_resource_info)}
            )

        response = self._factor_output_info
        if self._display_resource_info:
            response += "\n\n" + self._display_resource_info

        self.history_manager.add_assistant_message(response)
        self._agent_config_info = self._factor_output_info

        if self._progress_reporter:
            self._progress_reporter.complete_stage(
                "需求澄清完成",
                {"resource_count": len(self._resource_id_dict_info) if self._resource_id_dict_info else 0}
            )

        self.state = BuildState.PROCESSING
        return response

    def _handle_processing(
            self,
            query: str,
            dialog_history: List[Dict[str, str]]
    ) -> str:
        logger.info("开始处理智能体构建请求")

        messages_str = format_dialog_history(dialog_history)

        if self._intention_detector.detect_refine_intent(query, self._agent_config_info or ""):
            logger.info("检测到优化意图，开始重新澄清需求")
            
            if self._progress_reporter:
                self._progress_reporter.start_stage(
                    ProgressStage.CLARIFYING,
                    "检测到优化意图，正在重新澄清需求..."
                )

            self._factor_output_info, self._display_resource_info, self._resource_id_dict_info = (
                self._clarifier.clarify(
                    messages_str,
                    resource=self._resource
                )
            )

            if self._progress_reporter:
                self._progress_reporter.complete_stage(
                    "需求重新澄清完成",
                    {"has_resources": bool(self._display_resource_info)}
                )

            self._agent_config_info = self._factor_output_info
            self.history_manager.add_assistant_message(self._agent_config_info)
            
            logger.debug("修改后的agent要素配置：")
            logger.debug(self._agent_config_info)
            logger.debug(self._display_resource_info)
            
            return self._factor_output_info

        logger.info("开始生成 DSL")

        if self._progress_reporter:
            self._progress_reporter.start_stage(
                ProgressStage.GENERATING_CONFIG,
                "正在生成智能体配置...",
                {"has_config_info": bool(self._agent_config_info)}
            )

        constructor_output = self._generator.generate(
            message=messages_str,
            agent_config_info=self._agent_config_info or "",
            agent_resource_info=self._display_resource_info or "",
            resource_id_dict=self._resource_id_dict_info or {}
        )

        if self._progress_reporter:
            self._progress_reporter.complete_stage("配置生成完成")
            self._progress_reporter.start_stage(
                ProgressStage.TRANSFORMING_DSL,
                "正在转换为 DSL 格式...",
                {"output_length": len(str(constructor_output))}
            )

        dsl = self._transformer.transform_to_dsl(
            constructor_output,
            resource=self._resource
        )

        if self._progress_reporter:
            self._progress_reporter.complete_stage("DSL 转换完成")

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
        self._factor_output_info = None
        self._display_resource_info = None
        self._resource_id_dict_info = None
        logger.debug("LLM Agent 构建器内部状态已重置")

    def _update_resource(self, dialog_history: List[Dict[str, str]]) -> None:
        try:
            resource = self._retriever.retrieve(
                dialog_history,
                for_workflow=self._is_workflow_builder()
            )
            for key, value in resource.items():
                if key not in self._resource:
                    self._resource[key] = value
                    continue

                existing = self._resource[key]
                if isinstance(value, dict):
                    if isinstance(existing, dict):
                        existing.update(value)
                    else:
                        self._resource[key] = value
                    continue
                if isinstance(value, list):
                    unique_key = LlmAgentBuilder.RESOURCE_UNIQUE_KEY.get(key)
                    if unique_key is None:
                        continue

                    if not isinstance(existing, list):
                        existing = self._resource[key] = []

                    exist_keys = {item.get(unique_key) for item in existing if isinstance(item, dict)}
                    for item in value:
                        if not isinstance(item, dict):
                            continue
                        item_key = item.get(unique_key)
                        if item_key is None or item_key in exist_keys:
                            continue
                        existing.append(item)
                        exist_keys.add(item_key)
            logger.debug("资源更新完成", resource_keys=list(self._resource.keys()))
        except Exception as e:
            logger.warning("资源更新失败，继续使用现有资源", error=str(e))

    def _is_workflow_builder(self) -> bool:
        """
        Determine if workflow builder

        Returns:
            False (LLM Agent builder)
        """
        return False
