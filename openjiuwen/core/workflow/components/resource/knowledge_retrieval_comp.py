# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Knowledge Retrieval Workflow Component

Provides a workflow component that retrieves relevant documents from a knowledge base
"""

import asyncio
from dataclasses import field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.dataclasses import dataclass

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import LogEventType, workflow_logger
from openjiuwen.core.common.security.exception_utils import ExceptionUtils
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.graph.base import Graph
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.retrieval.common.config import (
    EmbeddingConfig,
    KnowledgeBaseConfig,
    RetrievalConfig,
    VectorStoreConfig,
)
from openjiuwen.core.retrieval.common.retrieval_result import MultiKBRetrievalResult
from openjiuwen.core.retrieval.graph_knowledge_base import GraphKnowledgeBase
from openjiuwen.core.retrieval.knowledge_base import KnowledgeBase
from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase, retrieve_multi_kb_with_source
from openjiuwen.core.retrieval.vector_store.store import create_vector_store
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.components.base import ComponentConfig
from openjiuwen.core.workflow.components.component import ComponentComposable, ComponentExecutable


@dataclass(kw_only=True)
class KnowledgeRetrievalCompConfig(ComponentConfig):
    kb_configs: List[KnowledgeBaseConfig]
    retrieval_config: RetrievalConfig
    vector_store_config: VectorStoreConfig
    # Additional config needed for vector store creation should be passed here
    vector_store_additional_config: Dict[str, Any]
    embed_config: Optional[EmbeddingConfig] = field(default=None)

    # Optional LLM config for agentic / query-rewrite scenarios
    model_id: Optional[str] = field(default=None)
    model_client_config: Optional[ModelClientConfig] = field(default=None)
    model_config: Optional[ModelRequestConfig] = field(default=None)

    # Output formatting
    result_separator: str = field(default="\n\n")
    include_metadata: bool = field(default=False)


class KnowledgeRetrievalInput(BaseModel):
    query: str
    model_config = ConfigDict(extra="allow")


class KnowledgeRetrievalOutput(BaseModel):
    results: List[str] = Field(default_factory=list)
    context: str = Field(default="")
    results_with_metadata: Optional[List[dict]] = Field(default=None)


# Executable (runtime logic)
class KnowledgeRetrievalExecutable(ComponentExecutable):
    def __init__(self, component_config: KnowledgeRetrievalCompConfig):
        super().__init__()
        self._config = component_config
        self._kb: List[KnowledgeBase] = []
        self._llm: Optional[Model] = None
        self._initialized: bool = False
        self._session: Optional[Session] = None
        self._lock = asyncio.Lock()

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """Execute a knowledge-base retrieval against the incoming query."""
        self._set_session(session)
        await self._initialize_if_needed()

        # Validate & extract query
        knowledge_retrieval_input = self.validate_inputs(inputs)
        query = knowledge_retrieval_input.query
        if not query.strip():
            raise build_error(
                StatusCode.COMPONENT_KNOWLEDGE_RETRIEVAL_INPUT_PARAM_ERROR,
                error_msg="Query must be a non-empty string",
            )

        retrieval_config = self._config.retrieval_config
        workflow_logger.info(
            "Knowledge retrieval started",
            event_type=LogEventType.WORKFLOW_COMPONENT_START,
            component_id=self._session.get_executable_id(),
            component_type_str="KnowledgeRetrievalComponent",
            session_id=self._session.get_session_id(),
            metadata={
                "query_length": len(query),
                "top_k": retrieval_config.top_k,
                "sensitive_mode": UserConfig.is_sensitive(),
            },
        )

        try:
            retrieval_results: List[MultiKBRetrievalResult] = await retrieve_multi_kb_with_source(
                kbs=self._kb,
                query=query,
                config=retrieval_config,
            )
        except Exception as e:
            workflow_logger.error(
                "Knowledge retrieval failed",
                event_type=LogEventType.WORKFLOW_COMPONENT_ERROR,
                component_id=self._session.get_executable_id(),
                component_type_str="KnowledgeRetrievalComponent",
                session_id=self._session.get_session_id(),
            )
            raise build_error(
                StatusCode.COMPONENT_KNOWLEDGE_RETRIEVAL_INVOKE_CALL_FAILED,
                error_msg=f"Retrieve call failed: {e}",
                cause=e,
            ) from e

        output = self._format_output(retrieval_results)

        workflow_logger.info(
            "Knowledge retrieval completed",
            event_type=LogEventType.WORKFLOW_COMPONENT_END,
            component_id=self._session.get_executable_id(),
            component_type_str="KnowledgeRetrievalComponent",
            session_id=self._session.get_session_id(),
            metadata={
                "num_results": len(retrieval_results),
                "sensitive_mode": UserConfig.is_sensitive(),
            },
        )

        return output

    def _set_session(self, session: Session):
        self._session = session

    async def _initialize_if_needed(self):
        """Lazily initialise the knowledge base on first invocation."""
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            try:
                if self._config.retrieval_config.agentic:
                    self._llm = await self._create_llm_instance()
                self._kb = await self._create_knowledge_base()
                self._initialized = True
            except Exception as e:
                raise build_error(
                    StatusCode.COMPONENT_KNOWLEDGE_RETRIEVAL_INVOKE_CALL_FAILED,
                    error_msg=f"Failed to initialise knowledge base: {e}",
                    cause=e,
                ) from e

    async def _create_knowledge_base(self) -> List[KnowledgeBase]:
        # Embed model needed for vector/hybrid search
        embed_model = None
        if self._config.embed_config is None:
            index_types = [kb_config.index_type for kb_config in self._config.kb_configs]
            if any(it in ("vector", "hybrid") for it in index_types):
                raise build_error(
                    StatusCode.COMPONENT_KNOWLEDGE_RETRIEVAL_EMBED_MODEL_INIT_ERROR,
                    error_msg="Embedding config is required for vector or hybrid index types",
                )
        else:
            from openjiuwen.core.retrieval.embedding.openai_embedding import OpenAIEmbedding

            try:
                embed_model = OpenAIEmbedding(config=self._config.embed_config)
            except Exception as e:
                raise build_error(
                    StatusCode.COMPONENT_KNOWLEDGE_RETRIEVAL_EMBED_MODEL_INIT_ERROR,
                    error_msg=f"Failed to initialize embedding model: {e}",
                    cause=e,
                ) from e

        # Vector store
        vector_store = create_vector_store(
            self._config.vector_store_config, **self._config.vector_store_additional_config
        )

        # Create knowledge base instances for retrieval
        use_graph = self._config.retrieval_config.use_graph
        kb_instances = []
        for kb_config in self._config.kb_configs:
            if use_graph:
                kb_instances.append(
                    GraphKnowledgeBase(
                        config=kb_config,
                        vector_store=vector_store,
                        embed_model=embed_model,
                        llm_client=self._llm,
                    )
                )
            else:
                kb_instances.append(
                    SimpleKnowledgeBase(
                        config=kb_config,
                        vector_store=vector_store,
                        embed_model=embed_model,
                        llm_client=self._llm,
                    )
                )
        return kb_instances

    async def _create_llm_instance(self) -> Model:
        if self._config.model_id is None:
            if self._config.model_client_config is None or self._config.model_config is None:
                raise build_error(
                    StatusCode.COMPONENT_KNOWLEDGE_RETRIEVAL_LLM_MODEL_INIT_ERROR,
                    error_msg="LLM model config is required for agentic retrieval",
                )
            return Model(self._config.model_client_config, self._config.model_config)
        else:
            from openjiuwen.core.runner import Runner

            return await Runner.resource_mgr.get_model(id=self._config.model_id)

    # input / output helpers
    @staticmethod
    def validate_inputs(inputs: Input) -> KnowledgeRetrievalInput:
        try:
            return KnowledgeRetrievalInput.model_validate(inputs)
        except ValidationError as e:
            raise build_error(
                StatusCode.COMPONENT_KNOWLEDGE_RETRIEVAL_INPUT_PARAM_ERROR,
                error_msg=ExceptionUtils.format_validation_error(e),
                cause=e,
            ) from e

    def _format_output(self, results: List[MultiKBRetrievalResult]) -> dict:
        texts = [r.text for r in results]
        context = self._config.result_separator.join(texts)

        output: Dict[str, Any] = {
            "results": texts,
            "context": context,
        }

        if self._config.include_metadata:
            output["results_with_metadata"] = [m.model_dump() for m in results]

        return output


class KnowledgeRetrievalComponent(ComponentComposable):
    def __init__(self, component_config: Optional[KnowledgeRetrievalCompConfig] = None):
        super().__init__()
        self._config = component_config

    def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False) -> None:
        graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)

    def to_executable(self) -> KnowledgeRetrievalExecutable:
        return KnowledgeRetrievalExecutable(self._config)
