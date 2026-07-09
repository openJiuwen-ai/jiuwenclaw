# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""ReActAgent Implementation

ReAct (Reasoning + Acting) paradigm Agent implementation

Created on: 2025-11-25
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from openjiuwen.core.common.exception.errors import FrameworkError, ModelError

from pydantic import Field, BaseModel

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig
)
from openjiuwen.core.context_engine import (
    ContextEngine,
    ContextEngineConfig,
    ModelContext
)
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    Model,
    UserMessage,
    SystemMessage
)
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.memory import LongTermMemory, MemoryScopeConfig
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.middleware.base import AgentCallbackEvent
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


# Module-level compiled regex patterns for detecting transient LLM errors.
# Using \b (word boundary) avoids false positives like "timeout" in
# "authentication_timeout_setting" or "connection" in "disconnection_policy".
_TRANSIENT_LLM_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"\bAPIConnectionError\b"),
    re.compile(r"\bConnectionError\b"),
    re.compile(r"\bconnection\s+refused\b", re.IGNORECASE),
    re.compile(r"\btimeout\b", re.IGNORECASE),
    re.compile(r"\btimed?\s*out\b", re.IGNORECASE),
    re.compile(r"\brate\s*limit\b", re.IGNORECASE),
    re.compile(r"\b429\b"),
    re.compile(r"\bServiceUnavailable\b"),
    re.compile(r"\b503\b"),
    re.compile(r"\bserver\s+error\b", re.IGNORECASE),
    re.compile(r"\bInternalServerError\b"),
    re.compile(r"\b500\b"),
    re.compile(r"\bgateway\s*timeout\b", re.IGNORECASE),
    re.compile(r"\b502\b"),
    re.compile(r"\b504\b"),
)


class ReActAgentConfig(BaseModel):
    mem_scope_id: str = Field(default="", description="Memory scope ID")
    model_name: str = Field(default="", description="Model name")
    model_provider: str = Field(default="openai", description="Model provider")
    api_key: str = Field(default="", description="API key")
    api_base: str = Field(default="", description="API base URL")
    prompt_template_name: str = Field(
        default="",
        description="Prompt template name"
    )
    prompt_template: List[Dict] = Field(
        default_factory=list,
        description="Prompt template list"
    )

    max_iterations: int = Field(default=5, description="Maximum iterations")

    # LLM retry configuration (for transient network errors)
    llm_max_retries: int = Field(
        default=3, ge=0, le=10,
        description="Max retry count for transient LLM call failures"
    )
    llm_retry_base_delay: float = Field(
        default=2.0, ge=0.5, le=60.0,
        description="Base delay in seconds for exponential backoff on LLM retry"
    )

    # LLM configuration objects (for Model initialization)
    model_client_config: Optional[ModelClientConfig] = Field(
        default=None,
        description="Model client configuration"
    )
    model_config_obj: Optional[ModelRequestConfig] = Field(
        default=None,
        description="Model request configuration"
    )

    sys_operation_id: Optional[str] = None

    context_engine_config: ContextEngineConfig = Field(
        default=ContextEngineConfig(
            max_context_message_num=200,
            default_window_round_num=10
        ),
        description="Context engine configuration"
    )

    context_processors: List[Tuple[str, BaseModel]] = Field(
        default=None,
        description="Context processors configuration"
    )

    def configure_model(self, model_name: str) -> 'ReActAgentConfig':
        """Configure model name

        Args:
            model_name: Model name

        Returns:
            self (supports chaining)
        """
        self.model_name = model_name
        return self

    def configure_model_provider(
            self,
            provider: str,
            api_key: str,
            api_base: str
    ) -> 'ReActAgentConfig':
        """Configure model provider details

        Args:
            provider: Model provider name (e.g., "openai")
            api_key: API key
            api_base: API base URL

        Returns:
            self (supports chaining)
        """
        self.model_provider = provider
        self.api_key = api_key
        self.api_base = api_base
        return self

    def configure_prompt(self, prompt_name: str) -> 'ReActAgentConfig':
        """Configure prompt template name

        Args:
            prompt_name: Prompt template name

        Returns:
            self (supports chaining)
        """
        self.prompt_template_name = prompt_name
        return self

    def configure_prompt_template(
            self,
            prompt_template: List[Dict]
    ) -> 'ReActAgentConfig':
        """Configure prompt template directly

        Args:
            prompt_template: Prompt template list, format like
                [{"role": "system", "content": "..."}]

        Returns:
            self (supports chaining)
        """
        self.prompt_template = prompt_template
        return self

    def configure_context_engine(
            self,
            max_context_message_num: Optional[int] = 200,
            default_window_round_num: Optional[int] = 10,
            enable_reload: bool = False
    ) -> 'ReActAgentConfig':
        """
        Configure the context-engine parameters that control how conversation history
        is truncated, offloaded and reloaded.

        Parameters
        ----------
        max_context_message_num : int, optional, default 200
            Hard upper bound on the total number of messages kept in the context
            window.  `None` means no hard limit.
        default_window_round_num : int, optional, default 10
            Number of **most-recent conversation rounds** to retain (a round =
            user message → final assistant reply without tool calls).  When set,
            it takes precedence over `default_window_message_num`.  Must be > 0
            if given.
        enable_reload : bool, default False
            Whether the agent is allowed to **automatically reload** messages that
            were previously off-loaded (via hints such as `[[OFFLOAD:...]]`).
            Enable this if you want the model to retrieve long content on demand;
            disable it to keep hints as plain text.
        """
        self.context_engine_config = ContextEngineConfig(
            max_context_message_num=max_context_message_num,
            default_window_round_num=default_window_round_num,
            enable_reload=enable_reload
        )
        return self


    def configure_mem_scope(self, mem_scope_id: str) -> 'ReActAgentConfig':
        """Configure memory scope ID

        Args:
            mem_scope_id: Memory scope ID

        Returns:
            self (supports chaining)
        """
        self.mem_scope_id = mem_scope_id
        return self

    def configure_max_iterations(
            self,
            max_iterations: int
    ) -> 'ReActAgentConfig':
        """Configure maximum iterations

        Args:
            max_iterations: Maximum number of ReAct loop iterations

        Returns:
            self (supports chaining)
        """
        self.max_iterations = max_iterations
        return self

    def configure_llm_retry(
            self,
            max_retries: int = 3,
            base_delay: float = 2.0
    ) -> 'ReActAgentConfig':
        """Configure LLM call retry behaviour for transient errors

        Args:
            max_retries: Maximum number of retry attempts (0-10)
            base_delay: Base delay in seconds for exponential backoff (0.5-60)

        Returns:
            self (supports chaining)
        """
        self.llm_max_retries = max_retries
        self.llm_retry_base_delay = base_delay
        return self

    def configure_model_client(
            self,
            provider: str,
            api_key: str,
            api_base: str,
            model_name: str,
            verify_ssl: bool = False
    ) -> 'ReActAgentConfig':
        """Configure model client for LLM initialization

        This method creates ModelClientConfig and ModelRequestConfig
        for the Model class initialization.

        Args:
            provider: Model provider name (e.g., "OpenAI", "SiliconFlow")
            api_key: API key
            api_base: API base URL
            model_name: Model name
            verify_ssl: Whether to verify SSL (default False)

        Returns:
            self (supports chaining)
        """
        self.model_provider = provider
        self.api_key = api_key
        self.api_base = api_base
        self.model_name = model_name

        self.model_client_config = ModelClientConfig(
            client_provider=provider,
            api_key=api_key,
            api_base=api_base,
            verify_ssl=verify_ssl
        )
        self.model_config_obj = ModelRequestConfig(
            model_name=model_name
        )
        return self

    def configure_context_processors(
            self,
            processors: List[Tuple[str, BaseModel]]
    ) -> 'ReActAgentConfig':
        self.context_processors = processors
        return self


class ReActAgent(BaseAgent):
    """ReAct paradigm Agent implementation
    ReAct loop: Reasoning -> Acting -> Observation -> Repeat

    Input format (compatible with legacy):
        {"query": "user question", "conversation_id": "session_123"}

    Output format (compatible with legacy):
        invoke: {"output": "response content", "result_type": "answer|error"}
        stream: yields OutputSchema objects

    Note:
        This agent currently does not support Runner.run_agent().
        Use agent.invoke() directly with a session parameter.
    """

    def __init__(
            self,
            card: AgentCard,
    ):
        """Initialize ReActAgent

        Args:
            card: Agent card (required)
        """
        self._config = self._create_default_config()
        self.context_engine = ContextEngine(
            self._config.context_engine_config
        )
        self._llm = None
        self._init_memory_scope()
        super().__init__(card)

    def _init_memory_scope(self) -> None:
        """Initialize memory scope (subclass can override configuration)"""
        if self._config.mem_scope_id:
            asyncio.run(
                LongTermMemory().set_scope_config(
                    self._config.mem_scope_id,
                    MemoryScopeConfig()
                )
            )

    def _create_default_config(self) -> ReActAgentConfig:
        """Create default configuration"""
        return ReActAgentConfig()

    def configure(self, config: ReActAgentConfig) -> 'BaseAgent':
        """Set configuration

        Args:
            config: ReActAgentConfig configuration object

        Returns:
            self (supports chaining)

        Note:
            After config update, context_engine and memory_scope
            will be updated accordingly
        """
        old_config = self._config
        self._config = config

        # Reset LLM if model config changed
        if (old_config.model_provider != config.model_provider or
                old_config.api_key != config.api_key or
                old_config.api_base != config.api_base):
            self._llm = None

        # Update context_engine if context window limit changed
        if old_config.context_engine_config != config.context_engine_config:
            self.context_engine = ContextEngine(
                config.context_engine_config
            )

        # Update memory_scope if memory scope ID changed
        if old_config.mem_scope_id != config.mem_scope_id:
            self._init_memory_scope()

        # Reset sys operation id if changed
        if old_config.sys_operation_id != config.sys_operation_id:
            self.lazy_init_skill()

        return self

    def _get_llm(self) -> Model:
        """Get LLM instance (lazy initialization)

        Returns:
            Model instance

        Raises:
            ValueError: If model_client_config is not configured
        """
        if self._llm is None:
            if self._config.model_client_config is None:
                raise ValueError(
                    "model_client_config is required. "
                    "Use configure_model_client() to set it."
                )
            self._llm = Model(
                model_client_config=self._config.model_client_config,
                model_config=self._config.model_config_obj
            )
        return self._llm

    async def _call_llm(
        self,
        messages: List,
        tools: Optional[List[ToolInfo]] = None
    ) -> AssistantMessage:
        """Call LLM with messages and optional tools, with retry on transient errors.

        Args:
            messages: Message list (BaseMessage or dict)
            tools: Optional tool definitions (List[ToolInfo])

        Returns:
            AssistantMessage from LLM

        Raises:
            FrameworkError / ModelError: Non-transient errors are re-raised directly;
                transient errors are retried up to llm_max_retries times.
        """
        llm = self._get_llm()
        max_retries = self._config.llm_max_retries
        base_delay = self._config.llm_retry_base_delay

        for attempt in range(1 + max_retries):
            try:
                return await llm.invoke(
                    model=self._config.model_name,
                    messages=messages,
                    tools=tools
                )
            except (FrameworkError, ModelError) as exc:
                exc_msg = str(exc)
                is_transient = any(p.search(exc_msg) for p in _TRANSIENT_LLM_PATTERNS)

                if not is_transient or attempt >= max_retries:
                    raise

                wait = base_delay * (2 ** attempt)
                logger.warning(
                    "LLM call transient error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, max_retries, wait, exc,
                )
                await asyncio.sleep(wait)
            except Exception as exc:
                # Non-LLM errors (e.g. programming bugs) are never transient
                raise

    async def _init_context(
            self,
            session: Optional[Session]
    ) -> ModelContext:
        if self._config.context_processors:
            from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter
            context = await self.context_engine.create_context(
                session=session,
                processors=self._config.context_processors,
                token_counter=TiktokenCounter()
            )
        else:
            context = await self.context_engine.create_context(
                session=session
            )
        context_reloader = context.reloader_tool()
        if self._config.context_engine_config.enable_reload:
            self.ability_manager.add(context_reloader.card)
            from openjiuwen.core.runner import Runner
            if not Runner.resource_mgr.get_tool(context_reloader.card.id, tag=self.card.id):
                Runner.resource_mgr.add_tool(context_reloader, tag=self.card.id)
        else:
            self.ability_manager.remove(context_reloader.card.name)
        return context

    async def invoke(
            self,
            inputs: Any,
            session: Optional[Session] = None
    ) -> Dict[str, Any]:
        """Execute ReAct process

        Args:
            inputs: User input, supports the following formats:
                - dict: {"query": "...", "conversation_id": "..."}
                - str: Used directly as query
            session: Session object (required for tool execution)

        Returns:
            Dict with output and result_type
        """
        # Normalize inputs
        if isinstance(inputs, dict):
            user_input = inputs.get("query")
            if user_input is None:
                raise ValueError("Input dict must contain 'query'")
        elif isinstance(inputs, str):
            user_input = inputs
        else:
            raise ValueError("Input must be dict with 'query' or str")

        # Hook: before invoke
        await self._execute_callbacks(AgentCallbackEvent.BEFORE_INVOKE, inputs=inputs)

        # Get or create model context
        context = await self._init_context(session)

        # Add user message to context
        await context.add_messages(UserMessage(content=user_input))

        # Build system messages from prompt template
        # prompt_template is List[Dict], access via dict keys
        system_messages = [
            SystemMessage(role=msg["role"], content=msg["content"])
            for msg in self._config.prompt_template
            if msg.get("role") == "system"
        ]

        if len(system_messages) > 0 and self._skill_util is not None and self._skill_util.has_skill():
            skill_prompt = self._skill_util.get_skill_prompt()
            last_msg = system_messages[-1]
            last_msg.content = (last_msg.content or "") + "\n" + skill_prompt

        # Get tool info from _ability_manager
        tools = await self.ability_manager.list_tool_info()

        result = None

        # ReAct loop
        for iteration in range(self._config.max_iterations):
            logger.info(
                f"ReAct iteration {iteration + 1}/{self._config.max_iterations}"
            )

            # Get context window with system messages and tools
            context_window = await context.get_context_window(
                system_messages=system_messages,
                tools=tools if tools else None,
            )

            # Hook: before model call
            await self._execute_callbacks(
                AgentCallbackEvent.BEFORE_MODEL_CALL,
                inputs=inputs,
                iteration=iteration + 1,
                messages=context_window.get_messages()
            )

            # Call LLM with messages and tools from context window
            ai_message = await self._call_llm(
                context_window.get_messages(),
                context_window.get_tools() or None
            )

            # Hook: after model call
            await self._execute_callbacks(
                AgentCallbackEvent.AFTER_MODEL_CALL,
                inputs=inputs,
                iteration=iteration + 1,
                response=ai_message
            )

            # Add AI message to context
            ai_msg_for_context = AssistantMessage(
                content=ai_message.content,
                tool_calls=ai_message.tool_calls
            )
            await context.add_messages(ai_msg_for_context)

            # Check for tool calls
            if ai_message.tool_calls:
                # Log tool calls
                for tool_call in ai_message.tool_calls:
                    logger.info(
                        f"Executing tool: {tool_call.name} "
                        f"with args: {tool_call.arguments}"
                    )

                    # Hook: before tool call
                    await self._execute_callbacks(
                        AgentCallbackEvent.BEFORE_TOOL_CALL,
                        inputs=inputs,
                        iteration=iteration + 1,
                        tool_name=tool_call.name,
                        tool_args=tool_call.arguments
                    )

                # Execute tools using _execute_ability (supports parallel)
                results = await self.ability_manager.execute(
                    ai_message.tool_calls, session, tag=self.card.id
                )

                # Process results and add tool messages to context
                for idx, (tool_result, tool_msg) in enumerate(results):
                    logger.info(f"Tool result: {tool_result}")
                    await context.add_messages(tool_msg)

                    # Hook: after tool call
                    tool_call = ai_message.tool_calls[idx]
                    await self._execute_callbacks(
                        AgentCallbackEvent.AFTER_TOOL_CALL,
                        inputs=inputs,
                        iteration=iteration + 1,
                        tool_name=tool_call.name,
                        tool_args=tool_call.arguments,
                        tool_result=tool_result
                    )
            else:
                # No tool calls, return AI response
                await self.context_engine.save_contexts(session)
                result = {
                    "output": ai_message.content,
                    "result_type": "answer"
                }
                # Hook: after invoke
                await self._execute_callbacks(
                    AgentCallbackEvent.AFTER_INVOKE,
                    inputs=inputs,
                    result=result
                )
                return result

        # Max iterations reached - generate fallback summary reply
        # Detect language from system_messages to keep reply language consistent
        try:
            combined_system = " ".join(
                getattr(msg, "content", "") or ""
                for msg in system_messages
            )
            is_chinese = bool(re.search(r"[\u4e00-\u9fff]", combined_system))
        except Exception:
            is_chinese = True

        if is_chinese:
            fallback_tip = (
                "任务已达到最大推理轮次限制，但尚未完成。"
                "请基于你已经做的工作和收集到的信息，"
                "给出一个阶段性的总结回复，说明已完成了哪些部分、"
                "遇到了什么限制（需要更多轮次才能完成），"
                "并给用户有用的阶段性结论。"
            )
        else:
            fallback_tip = (
                "The task has reached the maximum number of reasoning rounds without being completed. "
                "Based on the work you have done and the information you have gathered, "
                "provide a summary of what has been accomplished, "
                "what limitations were encountered (more rounds are needed to complete), "
                "and give the user useful interim conclusions."
            )

        # Get context window with system messages (no tools needed for summarizing)
        try:
            fallback_cw = await context.get_context_window(
                system_messages=system_messages,
                tools=None,
            )
            fallback_msgs = fallback_cw.get_messages()
            fallback_msgs.append(UserMessage(content=fallback_tip))
            fallback_reply = await self._call_llm(fallback_msgs, tools=None)
            fallback_output = fallback_reply.content or ""
        except Exception:
            logger.warning("Fallback summary generation failed, falling back to hard error")
            fallback_output = ""

        await self.context_engine.save_contexts(session)
        result = {
            "output": fallback_output or "Max iterations reached without completion",
            "result_type": "answer" if fallback_output else "error",
        }
        # Hook: after invoke
        await self._execute_callbacks(
            AgentCallbackEvent.AFTER_INVOKE,
            inputs=inputs,
            result=result
        )
        return result

    async def stream(
            self,
            inputs: Any,
            session: Optional[Session] = None,
            stream_modes: Optional[List[StreamMode]] = None
    ) -> AsyncIterator[Any]:
        """Stream execute ReAct process

        Args:
            inputs: User input (required in new version)
            session: Session object (required in new version)
            stream_modes: Stream output modes (optional)

        Yields:
            OutputSchema objects from stream_iterator
        """
        final_result_holder = {"result": None}

        if session is not None:
            await session.pre_run()

        async def stream_process():
            try:
                final_result = await self.invoke(inputs, session)
                final_result_holder["result"] = final_result
                # Write to session stream if available
                if session is not None:
                    await session.write_stream(OutputSchema(
                        type="answer",
                        index=0,
                        payload={
                            "output": final_result,
                            "result_type": "answer"
                        }
                    ))
            except Exception as e:
                logger.error(f"ReActAgent stream error: {e}")
                final_result_holder["result"] = {
                    "output": str(e),
                    "result_type": "error"
                }
            finally:
                # Close stream
                if session is not None:
                    await self.context_engine.save_contexts(session)
                    await session.post_run()

        task = asyncio.create_task(stream_process())

        # Read from stream_iterator and yield
        if session is not None:
            async for result in session.stream_iterator():
                yield result

        await task


__all__ = [
    "ReActAgent",
    "ReActAgentConfig",
]
