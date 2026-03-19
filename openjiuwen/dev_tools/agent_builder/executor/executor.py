# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Dict, Any, Optional
import asyncio

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.common.logging import LogManager
from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

from openjiuwen.dev_tools.agent_builder.builders.factory import AgentBuilderFactory
from openjiuwen.dev_tools.agent_builder.executor.history_manager import HistoryManager
from openjiuwen.dev_tools.agent_builder.builders.base import BaseAgentBuilder
from openjiuwen.dev_tools.agent_builder.utils.enums import AgentType
from openjiuwen.dev_tools.agent_builder.utils.progress import ProgressReporter, progress_manager

logger = LogManager.get_logger("agent_builder")


def _create_core_model(model_info: Optional[Dict[str, Any]]) -> Model:
    """Map model_info(dict) to core `Model` (no additional service layer wrapper)."""
    info = model_info or {}
    provider = info.get("model_provider") or info.get("client_provider")
    model_name = info.get("model_name") or info.get("model")
    api_key = info.get("api_key")
    api_base = info.get("api_base") or ""

    if not provider or not model_name or not api_key:
        raise ValidationError(
            StatusCode.COMPONENT_LLM_CONFIG_INVALID,
            msg="model_info missing required fields",
            details={
                "required": ["model_provider", "model_name", "api_key"],
                "got_keys": sorted(list(info.keys())),
            },
        )

    provider_map = {
        "openai": "OpenAI",
        "OpenAI": "OpenAI",
        "openrouter": "OpenRouter",
        "OpenRouter": "OpenRouter",
        "siliconflow": "SiliconFlow",
        "SiliconFlow": "SiliconFlow",
        "dashscope": "DashScope",
        "DashScope": "DashScope",
    }
    client_provider = provider_map.get(provider, provider)

    client_config = ModelClientConfig(
        client_provider=client_provider,
        client_id=str(model_name),
        api_key=str(api_key),
        api_base=str(api_base),
        verify_ssl=bool(info.get("verify_ssl", False)),
    )
    request_config = ModelRequestConfig(
        model=str(model_name),
        temperature=info.get("temperature"),
        max_tokens=info.get("max_tokens"),
        top_p=info.get("top_p"),
        timeout=info.get("timeout"),
    )
    return Model(model_client_config=client_config, model_config=request_config)


class AgentBuilderExecutor:
    """Agent Builder Executor

    Unified entry class responsible for creating corresponding builders
    based on agent_type and executing the build process.
    Now uses unified interface, no longer distinguishes between
    LLM Agent and Workflow Agent invocation.
    """

    def __init__(
        self,
        query: str,
        session_id: str,
        agent_type: str,
        history_manager_map: Dict[str, HistoryManager],
        agent_builder_map: Optional[Dict[str, BaseAgentBuilder]] = None,
        model_info: Optional[Dict[str, Any]] = None,
        enable_progress: bool = True,
    ) -> None:
        """
        Initialize executor

        Args:
            query: User query
            session_id: Session ID
            agent_type: Agent type ('llm_agent' or 'workflow')
            history_manager_map: Session history manager map (for cross-session reuse)
            agent_builder_map: Builder map (optional, for cross-session reuse)
            model_info: Model configuration info (optional)
            enable_progress: Whether to enable progress reporting, default True

        Raises:
            ValidationError: When parameters are invalid (model_info config error)
        """
        self.query: str = query
        self.session_id: str = session_id
        self.agent_type: str = agent_type

        self.llm: Model = _create_core_model(model_info)

        self.history_manager: HistoryManager = self._get_history_manager(
            session_id,
            history_manager_map,
        )

        self.progress_reporter: Optional[ProgressReporter] = None
        if enable_progress:
            self.progress_reporter = progress_manager.create_reporter(
                session_id,
                agent_type,
            )

        if agent_builder_map is None:
            agent_builder_map = {}

        self.agent_builder: BaseAgentBuilder = self._get_agent_builder(
            session_id,
            agent_builder_map,
        )

    @staticmethod
    def _get_history_manager(
        session_id: str,
        history_manager_map: Dict[str, HistoryManager],
    ) -> HistoryManager:
        """
        Get or create session history manager
        """
        if session_id not in history_manager_map:
            history_manager = HistoryManager()
            history_manager_map[session_id] = history_manager
            logger.debug("Created new session history manager", session_id=session_id)
            return history_manager

        logger.debug("Reusing existing session history manager", session_id=session_id)
        return history_manager_map[session_id]

    def _get_agent_builder(
        self,
        session_id: str,
        agent_builder_map: Dict[str, BaseAgentBuilder],
    ) -> BaseAgentBuilder:
        """
        Get or create agent builder
        """
        if session_id not in agent_builder_map:
            try:
                agent_type_enum = AgentType(self.agent_type)
            except ValueError as e:
                error_msg = f"Unsupported agent type: {self.agent_type}"
                logger.error(
                    "Unsupported agent type",
                    agent_type=self.agent_type,
                )
                raise ValidationError(
                    StatusCode.AGENT_BUILDER_AGENT_TYPE_NOT_SUPPORTED,
                    msg=error_msg,
                    agent_type=self.agent_type,
                    supported_types=[t.value for t in AgentType],
                ) from e

            builder = AgentBuilderFactory.create(
                agent_type_enum,
                self.llm,
                self.history_manager,
            )

            if self.progress_reporter:
                setattr(builder, "_progress_reporter", self.progress_reporter)

            agent_builder_map[session_id] = builder
            logger.debug(
                "Created builder instance",
                session_id=session_id,
                agent_type=self.agent_type,
            )
            return builder

        logger.debug("Reusing existing builder", session_id=session_id)
        return agent_builder_map[session_id]

    def execute(self) -> Any:
        """
        Execute build process
        """
        logger.info(
            "Starting build execution",
            session_id=self.session_id,
            agent_type=self.agent_type,
            query_length=len(self.query),
        )

        try:
            self.history_manager.add_user_message(self.query)

            result = self.agent_builder.execute(self.query)

            logger.info(
                "Build execution completed",
                session_id=self.session_id,
                agent_type=self.agent_type,
                result_type=type(result).__name__,
            )

            return result

        except Exception as e:
            logger.error(
                "Build execution failed",
                session_id=self.session_id,
                agent_type=self.agent_type,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise

    def get_build_status(self) -> Dict[str, Any]:
        """
        Get build status
        """
        status = self.agent_builder.get_build_status()
        return {
            "session_id": self.session_id,
            "agent_type": self.agent_type,
            **status,
        }
