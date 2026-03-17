# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""ReActAgent Implementation

ReAct (Reasoning + Acting) paradigm Agent implementation

Created on: 2025-11-25
Author: huenrui1@huawei.com
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from pydantic import Field, BaseModel

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.user_config import UserConfig
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
    ToolMessage,
    UserMessage,
    SystemMessage
)
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.session.agent import Session, create_agent_session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackEvent,
    AgentCallbackContext,
    InvokeInputs,
    ModelCallInputs,
    rail,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _summarize_tool_call(tc: Any) -> str:
    """Format a single tool call for logging."""
    if isinstance(tc, dict):
        fn = tc.get("function", {})
        return f"{fn.get('name', '?')}({str(fn.get('arguments', ''))[:100]})"
    fn = getattr(tc, "function", tc)
    name = getattr(fn, "name", getattr(tc, "name", "?"))
    args = str(getattr(fn, "arguments", getattr(tc, "arguments", "")))[:100]
    return f"{name}({args})"


def log_llm_request(
    log: Any,
    messages: Optional[List[Any]],
    tools: Optional[List[Any]],
) -> None:
    """Log LLM request messages and tools."""
    msgs = messages or []
    tool_count = len(tools) if tools else 0
    log.info(
        f"[LLM] >>> request: msg_count={len(msgs)}, "
        f"tool_count={tool_count}"
    )
    if UserConfig.is_sensitive():
        return
    for idx, msg in enumerate(msgs):
        if isinstance(msg, dict):
            role = msg.get("role", "")
            content = str(msg.get("content", ""))
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id", "")
        else:
            role = getattr(msg, "role", "")
            content = str(getattr(msg, "content", ""))
            tool_calls = getattr(msg, "tool_calls", None)
            tool_call_id = getattr(msg, "tool_call_id", "")
        parts: List[str] = [f"[LLM]   msg[{idx}] role={role}"]
        if content:
            parts.append(f"content={content[:300]}")
        if tool_calls:
            tc_summary = [_summarize_tool_call(tc) for tc in tool_calls]
            parts.append(f"tool_calls=[{', '.join(tc_summary)}]")
        if tool_call_id:
            parts.append(f"tool_call_id={tool_call_id}")
        log.info(", ".join(parts))


def log_llm_response(log: Any, ai_message: Any) -> None:
    """Log LLM response content and tool calls."""
    usage = getattr(ai_message, "usage_metadata", None)
    usage_str = ""
    if usage:
        usage_str = (
            f", tokens={{input={getattr(usage, 'input_tokens', '?')}, "
            f"output={getattr(usage, 'output_tokens', '?')}}}"
        )
    if UserConfig.is_sensitive():
        tc_count = len(ai_message.tool_calls) if ai_message.tool_calls else 0
        log.info(
            f"[LLM] <<< response: "
            f"content_len={len(ai_message.content or '')}, "
            f"tool_call_count={tc_count}{usage_str}"
        )
    else:
        log.info(
            f"[LLM] <<< response: "
            f"content={ai_message.content or ''}{usage_str}"
        )
        if ai_message.tool_calls:
            for tc in ai_message.tool_calls:
                log.info(
                    f"[LLM]   tool_call: "
                    f"{tc.name}({tc.arguments})"
                )


class ReActAgentConfig(BaseModel):
    """ReActAgent Configuration Class

    Attributes:
        max_iterations: Maximum number of ReAct loop iterations
    """
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
        if self.model_config_obj is None:
            self.model_config_obj = ModelRequestConfig(model_name=model_name)
        else:
            self.model_config_obj.model_name = model_name
        return self

    def configure_context_processors(
            self,
            processors: List[Tuple[str, BaseModel]]
    ) -> 'ReActAgentConfig':
        self.context_processors = processors
        return self


_INTERRUPTION_KEY = "__react_agent_interruption__"


@dataclass
class WorkflowInterruptEntry:
    """Per-workflow interruption record."""
    tool_call: Any
    component_ids: List[str]
    workflow_execution_state: Any
    collected_input: Any = None  # None means not yet collected


@dataclass
class InterruptionState:
    """Workflow interruption state for resume support.

    interrupted_workflows: per-workflow entries keyed by workflow_id
    pending_workflow_id: workflow_id currently waiting for user feedback
    pending_component_id: component_id currently waiting for user feedback
    original_query: the original user query from the first invoke (before any resume)
    """
    ai_message: AssistantMessage
    iteration: int
    interrupted_workflows: Dict[str, WorkflowInterruptEntry]
    pending_workflow_id: str
    pending_component_id: str
    original_query: str = ""


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
        super().__init__(card)
        self._ability_manager.set_context_engine(self.context_engine)

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
            self._ability_manager.set_context_engine(self.context_engine)
        # Reset sys operation id if changed
        if old_config.sys_operation_id != config.sys_operation_id:
            self.lazy_init_skill()

        return self

    def set_llm(self, llm: Model) -> None:
        """Set LLM model instance directly.

        Args:
            llm: Pre-built Model instance to use.
        """
        self._llm = llm

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

    async def _call_model(
            self,
            ctx: AgentCallbackContext,
            context: ModelContext,
            system_messages: List,
            tools: Optional[List[ToolInfo]],
    ) -> AssistantMessage:
        """Prepare ctx.inputs for model call, then invoke @railed method.

        Args:
            ctx: Shared AgentCallbackContext for this invoke
            context: Current ModelContext
            system_messages: System messages for context window
            tools: Tool definitions

        Returns:
            AssistantMessage from LLM
        """
        context_window = await context.get_context_window(
            system_messages=system_messages,
            tools=tools if tools else None,
        )
        ctx.inputs = ModelCallInputs(
            messages=context_window.get_messages(),
            tools=context_window.get_tools(),
        )

        log_llm_request(logger, ctx.inputs.messages, ctx.inputs.tools)

        ai_message = await self._railed_model_call(ctx)

        log_llm_response(logger, ai_message)

        return ai_message

    @rail(
        before=AgentCallbackEvent.BEFORE_MODEL_CALL,
        after=AgentCallbackEvent.AFTER_MODEL_CALL,
        on_exception=AgentCallbackEvent.ON_MODEL_EXCEPTION,
    )
    async def _railed_model_call(self, ctx: AgentCallbackContext) -> AssistantMessage:
        """Execute LLM call with @rail before/after/on_exception hooks.

        Uses llm.stream() when ctx.extra["_streaming"] is True,
        falls back to llm.invoke() otherwise.
        Rail hooks may have modified ctx.inputs.messages / ctx.inputs.tools.
        """
        llm = self._get_llm()
        session = ctx.session

        if not ctx.extra.get("_streaming"):
            ai_message = await llm.invoke(
                model=self._config.model_name,
                messages=ctx.inputs.messages,
                tools=ctx.inputs.tools or None,
            )
            ctx.inputs.response = ai_message
            return ai_message

        # Streaming path: accumulate chunks via __add__, write to session in real-time
        accumulated_chunk = None
        chunk_index = 0

        async for chunk in llm.stream(
            model=self._config.model_name,
            messages=ctx.inputs.messages,
            tools=ctx.inputs.tools or None,
        ):
            if accumulated_chunk is None:
                accumulated_chunk = chunk
            else:
                accumulated_chunk = accumulated_chunk + chunk

            if chunk.reasoning_content:
                await session.write_stream(OutputSchema(
                    type="llm_reasoning",
                    index=chunk_index,
                    payload={"content": chunk.reasoning_content, "result_type": "answer"},
                ))
                chunk_index += 1
            if chunk.content:
                await session.write_stream(OutputSchema(
                    type="llm_output",
                    index=chunk_index,
                    payload={"content": chunk.content, "result_type": "answer"},
                ))
                chunk_index += 1

        if accumulated_chunk is None:
            ai_message = AssistantMessage(content="", tool_calls=[])
        else:
            ai_message = AssistantMessage(
                content=accumulated_chunk.content or "",
                tool_calls=accumulated_chunk.tool_calls or [],
                usage_metadata=accumulated_chunk.usage_metadata,
                reasoning_content=accumulated_chunk.reasoning_content,
            )
        ctx.inputs.response = ai_message
        return ai_message

    async def _execute_tool_call(
            self,
            ctx: AgentCallbackContext,
            tool_calls: List,
            session: Optional[Session],
            context: ModelContext,
    ) -> list:
        """Execute tool calls in parallel and commit tool messages into context.

        Returns:
            List of (tool_result, tool_message) tuples from ability_manager
        """
        if not tool_calls:
            return []

        for tool_call in tool_calls:
            logger.info(f"Executing tool: {tool_call.name} with args: {tool_call.arguments}")

        results = await self.ability_manager.execute(ctx=ctx, tool_call=tool_calls, session=session)

        for _, tool_message in results:
            if tool_message is not None:
                await context.add_messages(tool_message)

        return results

    def _is_interrupted(self, tool_result: Any) -> bool:
        """Detect whether a tool result signals workflow interruption."""
        from openjiuwen.core.workflow import WorkflowOutput, WorkflowExecutionState
        if isinstance(tool_result, WorkflowOutput):
            return tool_result.state == WorkflowExecutionState.INPUT_REQUIRED
        if isinstance(tool_result, list):
            return any(
                hasattr(item, "type") and item.type == "__interaction__"
                for item in tool_result
            )
        return False

    def _extract_component_ids(self, tool_result: Any) -> List[str]:
        """Extract component IDs from an interrupted workflow result, sorted for stability."""
        from openjiuwen.core.workflow import WorkflowOutput
        if isinstance(tool_result, WorkflowOutput) and isinstance(tool_result.result, list):
            ids = []
            for item in tool_result.result:
                if (hasattr(item, "type") and item.type == "__interaction__" and
                        hasattr(item, "payload") and hasattr(item.payload, "id")):
                    ids.append(item.payload.id)
            return sorted(ids)
        if isinstance(tool_result, list):
            ids = []
            for item in tool_result:
                if (hasattr(item, "type") and item.type == "__interaction__" and
                        hasattr(item, "payload") and isinstance(item.payload, dict)):
                    ids.append(item.payload.get("component_id", ""))
            return sorted(ids)
        return []

    def _extract_workflow_id(self, tool_call: Any) -> str:
        """Resolve workflow_id from tool_call.name via ability_manager."""
        for ability in self._ability_manager.list():
            if ability.name == tool_call.name:
                return ability.id
        return tool_call.name  # fallback

    def _after_execute_tool_call(
            self,
            results: list,
            tool_calls: list,
            ai_message: AssistantMessage,
            iteration: int,
            original_query: str = "",
    ) -> Optional[InterruptionState]:
        """Check tool results for interruption and build InterruptionState if found.

        Collects ALL interrupted workflows into interrupted_workflows dict.
        Sets pending_workflow_id/pending_component_id to the first interrupted one.

        Returns:
            InterruptionState if any result is interrupted, else None
        """
        interrupted: Dict[str, WorkflowInterruptEntry] = {}
        first_workflow_id = None
        first_component_id = None

        for i, (tool_result, _) in enumerate(results):
            if self._is_interrupted(tool_result):
                workflow_id = self._extract_workflow_id(tool_calls[i])
                component_ids = self._extract_component_ids(tool_result)
                interrupted[workflow_id] = WorkflowInterruptEntry(
                    tool_call=tool_calls[i],
                    component_ids=component_ids,
                    workflow_execution_state=tool_result,
                )
                if first_workflow_id is None:
                    first_workflow_id = workflow_id
                    first_component_id = component_ids[0] if component_ids else ""

        if not interrupted:
            return None

        return InterruptionState(
            ai_message=ai_message,
            iteration=iteration,
            interrupted_workflows=interrupted,
            pending_workflow_id=first_workflow_id,
            pending_component_id=first_component_id,
            original_query=original_query,
        )

    def _save_interruption_state(self, state: InterruptionState, session) -> None:
        if session:
            session.update_state({_INTERRUPTION_KEY: state})

    def _load_interruption_state(self, session) -> Optional[InterruptionState]:
        if session:
            return session.get_state(_INTERRUPTION_KEY)
        return None

    def _clear_interruption_state(self, session) -> None:
        if session:
            session.update_state({_INTERRUPTION_KEY: None})

    def _build_interrupt_result(self, state: InterruptionState) -> Dict[str, Any]:
        """Build the interrupt result dict returned to the caller."""
        pending_entry = state.interrupted_workflows[state.pending_workflow_id]
        return {
            "result_type": "interrupt",
            "workflow_execution_state": pending_entry.workflow_execution_state,
            "component_ids": [state.pending_component_id],
        }

    async def _commit_interrupt(
            self,
            interrupt: InterruptionState,
            context: ModelContext,
            session: Optional[Session],
            invoke_inputs: InvokeInputs,
    ) -> Dict[str, Any]:
        """Persist interruption state and return the interrupt result dict.

        Writes a single placeholder ToolMessage for the pending workflow's tool_call.
        """
        pending_entry = interrupt.interrupted_workflows[interrupt.pending_workflow_id]
        await context.add_messages(ToolMessage(
            tool_call_id=pending_entry.tool_call.id,
            content="[INTERRUPTED - Waiting for user input]",
        ))
        await self.context_engine.save_contexts(session)
        self._save_interruption_state(interrupt, session)
        result = self._build_interrupt_result(interrupt)
        invoke_inputs.result = result
        return result

    async def _handle_resume(
            self,
            interruption_state: InterruptionState,
            user_input: Any,
            ctx: AgentCallbackContext,
            context: ModelContext,
            session: Optional[Session],
            *,
            invoke_inputs: InvokeInputs,
    ) -> Optional[Dict[str, Any]]:
        """Process one resume step.

        Collects user feedback for the pending workflow/component.
        Triggers concurrent resume only when ALL interrupted workflows have feedback.

        Returns interrupt result dict if still waiting, or None to continue ReAct loop.
        """
        import copy

        resume_iteration = interruption_state.iteration
        logger.info(f"Resuming ReAct from iteration {resume_iteration + 1}")

        # Step 1: record feedback for the current pending workflow
        pending_wf_id = interruption_state.pending_workflow_id
        pending_comp_id = interruption_state.pending_component_id
        pending_entry = interruption_state.interrupted_workflows[pending_wf_id]

        interactive_input = self._build_interactive_input(user_input, [pending_comp_id])
        pending_entry.collected_input = interactive_input

        # Step 2: check if all interrupted workflows have collected feedback
        all_collected = all(
            entry.collected_input is not None
            for entry in interruption_state.interrupted_workflows.values()
        )

        if not all_collected:
            # Find next workflow without feedback and commit interrupt for it
            for wf_id, entry in interruption_state.interrupted_workflows.items():
                if entry.collected_input is None:
                    next_comp_id = entry.component_ids[0] if entry.component_ids else ""
                    interruption_state.pending_workflow_id = wf_id
                    interruption_state.pending_component_id = next_comp_id
                    return await self._commit_interrupt(interruption_state, context, session, invoke_inputs)

        # Step 3: all feedbacks collected — write ai_message and concurrently resume all workflows
        resume_ai_message = copy.deepcopy(interruption_state.ai_message)
        await context.add_messages(resume_ai_message)

        all_tool_calls = []
        for entry in interruption_state.interrupted_workflows.values():
            tc_copy = copy.deepcopy(entry.tool_call)
            tc_copy.arguments = entry.collected_input
            all_tool_calls.append(tc_copy)

        results = await self._execute_tool_call(ctx, all_tool_calls, session, context)
        interrupt = self._after_execute_tool_call(
            results, all_tool_calls, resume_ai_message, resume_iteration,
            original_query=interruption_state.original_query,
        )
        if interrupt:
            return await self._commit_interrupt(interrupt, context, session, invoke_inputs)

        # All workflows completed — continue ReAct loop from next iteration
        ctx.extra["_resume_start_iteration"] = resume_iteration + 1
        return None

    def _extract_user_text(self, user_input: Any) -> str:
        """Extract plain text from user_input (supports InteractiveInput or str)."""
        from openjiuwen.core.session import InteractiveInput
        if isinstance(user_input, InteractiveInput):
            if user_input.user_inputs:
                return str(next(iter(user_input.user_inputs.values())))
            if user_input.raw_inputs is not None:
                return str(user_input.raw_inputs)
            return ""
        return str(user_input)

    def _build_interactive_input(self, user_query: Any, component_ids: List[str]) -> Any:
        """Build an InteractiveInput from user feedback and component IDs."""
        from openjiuwen.core.session import InteractiveInput
        if isinstance(user_query, InteractiveInput):
            provided_ids = set(user_query.user_inputs.keys())
            fallback = next(iter(user_query.user_inputs.values()), "") if provided_ids else ""
            for comp_id in component_ids:
                if comp_id not in provided_ids:
                    user_query.update(comp_id, fallback)
            return user_query
        if component_ids:
            interactive_input = InteractiveInput()
            for comp_id in component_ids:
                interactive_input.update(comp_id, str(user_query))
            return interactive_input
        return InteractiveInput(raw_inputs=str(user_query))

    @staticmethod
    def _render_system_messages(system_messages: List, inputs: Any) -> None:
        """Render inputs fields into system message placeholders in-place.

        Aligns with MessageHandlerUtils.format_llm_inputs: InteractiveInput skips rendering.
        Only str-valued fields from inputs dict are used as render variables.
        """
        from openjiuwen.core.session import InteractiveInput
        from openjiuwen.core.foundation.prompt import PromptTemplate
        if isinstance(inputs, InteractiveInput):
            return
        if isinstance(inputs, dict):
            render_fields = {k: v for k, v in inputs.items() if isinstance(v, str)}
        else:
            render_fields = {"query": str(inputs)}
        if not render_fields:
            return
        for msg in system_messages:
            if not isinstance(msg.content, str):
                continue
            try:
                msg.content = PromptTemplate(content=msg.content).format(render_fields).content
            except BaseError as e:
                logger.warning("Failed to render system message placeholder: %s", e)

    async def _warn_missing_skill_read_file_tool(self) -> None:
        """
        Log a warning when skill prompt is enabled but the required read_file tool
        is missing from ability_manager.
        """
        tool_infos = await self.ability_manager.list_tool_info()

        has_read_file = False
        existing_tool_names: List[str] = []

        for t in tool_infos or []:
            name = getattr(t, "name", None)
            if isinstance(name, str) and name:
                existing_tool_names.append(name)
                if name == "read_file":
                    has_read_file = True

        if has_read_file:
            return

        from openjiuwen.core.common.exception.codes import StatusCode
        from openjiuwen.core.common.exception.errors import build_error

        err = build_error(
            StatusCode.AGENT_TOOL_NOT_FOUND,
            error_msg=(
                "skill prompt requires tool 'read_file' but it is not found in ability_manager. "
                f"existing_tools={sorted(set(existing_tool_names))}"
            )
        )
        logger.warning(str(err))

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
            session: Optional[Session] = None,
            **kwargs,
    ) -> Dict[str, Any]:
        """Execute ReAct process

        Args:
            inputs: User input, supports the following formats:
                - dict: {"query": "...", "conversation_id": "..."}
                - str: Used directly as query
            session: Session object (required for tool execution)
            **kwargs: Internal flags. _streaming=True selects
                the llm.stream() path inside _railed_model_call.

        Returns:
            Dict with output and result_type
        """
        if not isinstance(inputs, (dict, str)):
            raise ValueError("Input must be dict with 'query' or str")

        if isinstance(inputs, dict):
            query = inputs.get("query", "")
            conversation_id = inputs.get("conversation_id")
        else:
            query = inputs
            conversation_id = None

        # Auto-create session when not provided
        need_cleanup = False
        if session is None:
            session_id = conversation_id or "default_session"
            session = create_agent_session(
                session_id=session_id, card=self.card
            )
            await session.pre_run(inputs=inputs if isinstance(inputs, dict) else None)
            need_cleanup = True

        invoke_inputs = InvokeInputs(query=query, conversation_id=conversation_id)
        ctx = AgentCallbackContext(agent=self, inputs=invoke_inputs, session=session)
        ctx.extra["_streaming"] = kwargs.get("_streaming", False)
        if isinstance(inputs, dict):
            ctx.extra["user_id"] = inputs.get("user_id", "")

        try:
            async with ctx.lifecycle(AgentCallbackEvent.BEFORE_INVOKE, AgentCallbackEvent.AFTER_INVOKE):
                user_input = ctx.inputs.query
                if not user_input:
                    raise ValueError("Input must contain 'query'")

                interruption_state = self._load_interruption_state(session)
                if interruption_state is not None:
                    self._clear_interruption_state(session)
                    # Restore original query so MemoryRail.after_invoke writes the right UserMessage
                    ctx.extra["_original_query"] = interruption_state.original_query

                context = await self._init_context(session)
                ctx.context = context

                system_messages = [
                    SystemMessage(role=msg["role"], content=msg["content"])
                    for msg in self._config.prompt_template
                    if msg.get("role") == "system"
                ]
                self._render_system_messages(system_messages, inputs)

                if system_messages and self._skill_util is not None and self._skill_util.has_skill():
                    await self._warn_missing_skill_read_file_tool()
                    skill_prompt = self._skill_util.get_skill_prompt()
                    system_messages[-1].content = (system_messages[-1].content or "") + "\n" + skill_prompt

                tools = await self.ability_manager.list_tool_info()
                await context.add_messages(UserMessage(content=self._extract_user_text(user_input)))

                start_iteration = 0
                if interruption_state is not None:
                    resume_result = await self._handle_resume(
                        interruption_state, user_input, ctx, context, session, invoke_inputs=invoke_inputs
                    )
                    if resume_result is not None:
                        pass  # invoke_inputs.result already set by _handle_resume/_commit_interrupt
                    else:
                        start_iteration = ctx.extra.pop("_resume_start_iteration", 0)

                if invoke_inputs.result is None:
                    for iteration in range(start_iteration, self._config.max_iterations):
                        logger.info(f"ReAct iteration {iteration + 1}/{self._config.max_iterations}")

                        ai_message = await self._call_model(ctx, context, system_messages, tools)
                        await context.add_messages(
                            AssistantMessage(content=ai_message.content, tool_calls=ai_message.tool_calls)
                        )

                        if not ai_message.tool_calls:
                            await self.context_engine.save_contexts(session)
                            result = {"output": ai_message.content, "result_type": "answer"}
                            invoke_inputs.result = result
                            break

                        results = await self._execute_tool_call(ctx, ai_message.tool_calls, session, context)
                        interrupt = self._after_execute_tool_call(
                            results, ai_message.tool_calls, ai_message, iteration,
                            original_query=ctx.extra.get("_original_query", ""),
                        )
                        if interrupt:
                            await self._commit_interrupt(interrupt, context, session, invoke_inputs)
                            break
                    else:
                        await self.context_engine.save_contexts(session)
                        result = {"output": "Max iterations reached without completion", "result_type": "error"}
                        invoke_inputs.result = result

            # after_invoke rails have fired; return result (possibly adapted by rails via ctx.extra)
            return ctx.extra.get("invoke_result", invoke_inputs.result)
        finally:
            if need_cleanup:
                await self.context_engine.save_contexts(session)
                await session.post_run()

    async def _write_invoke_result_to_stream(
            self,
            result: Dict[str, Any],
            session: Session,
    ) -> None:
        """Write the final invoke result to the session stream.

        For interrupt results, only emit the single OutputSchema whose payload.id
        matches the pending_component_id, so callers see exactly one interaction chunk.
        """
        result_type = result.get("result_type", "")
        if result_type == "interrupt":
            workflow_state = result.get("workflow_execution_state")
            component_ids = result.get("component_ids", [])
            pending_id = component_ids[0] if component_ids else None
            schemas = (
                workflow_state.result
                if workflow_state is not None
                and isinstance(getattr(workflow_state, "result", None), list)
                else []
            )
            for schema in schemas:
                if (pending_id is None
                        or (hasattr(schema, "payload")
                            and hasattr(schema.payload, "id")
                            and schema.payload.id == pending_id)):
                    await session.write_stream(schema)
        else:
            await session.write_stream(OutputSchema(
                type="answer",
                index=0,
                payload={"output": result.get("output", ""), "result_type": result_type},
            ))

    async def stream(
            self,
            inputs: Any,
            session: Optional[Session] = None,
            stream_modes: Optional[List[StreamMode]] = None
    ) -> AsyncIterator[Any]:
        """Stream execute ReAct process

        Args:
            inputs: User input
            session: Session object (if None, auto create)
            stream_modes: Stream output modes (optional)

        Yields:
            OutputSchema objects from stream_iterator
        """
        # Auto-create session when not provided
        need_cleanup = False
        if session is None:
            if isinstance(inputs, dict):
                conversation_id = inputs.get("conversation_id")
            else:
                conversation_id = None
            session_id = conversation_id or "default_session"
            session = create_agent_session(
                session_id=session_id, card=self.card
            )
            need_cleanup = True

        await session.pre_run(
            inputs=inputs if isinstance(inputs, dict) else None
        )

        async def stream_process():
            try:
                final_result = await self.invoke(inputs, session, _streaming=True)
                if isinstance(final_result, list):
                    for schema in final_result:
                        await session.write_stream(schema)
                else:
                    await self._write_invoke_result_to_stream(
                        final_result, session
                    )
            except Exception as e:
                logger.error(f"ReActAgent stream error: {e}")
            finally:
                if need_cleanup:
                    await self.context_engine.save_contexts(session)
                await session.post_run()

        task = asyncio.create_task(stream_process())

        async for result in session.stream_iterator():
            yield result

        await task

    async def clear_session(self, session_id: str = "default_session"):
        """Release session resources and clear context cache."""
        from openjiuwen.core.runner import Runner
        await Runner.release(session_id=session_id)
        await self.context_engine.clear_context(session_id=session_id)


__all__ = [
    "ReActAgent",
    "ReActAgentConfig",
]
