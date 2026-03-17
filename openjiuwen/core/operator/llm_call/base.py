from __future__ import annotations

# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
LLM invocation operator with prompt tunables.

LLMCallOperator wraps LLM calls with:
- Prompt tunables (system_prompt, user_prompt) for self-evolution
- Streaming support
- Backward compatible alias: LLMCall
"""

from typing import Dict, Any, Optional, List, Union, AsyncIterator, Callable

from openjiuwen.core.session.agent import Session
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.llm import BaseMessage, AssistantMessage, SystemMessage, Model
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.operator.base import Operator, TunableSpec

DEFAULT_USER_PROMPT: str = "{{query}}"


class LLMCallOperator(Operator):
    """LLM invocation operator with prompt tunables.

    Executes LLM calls and supports prompt optimization via tunables.
    """

    def __init__(
        self,
        model_name: str,
        llm: Model,
        system_prompt: str | List[BaseMessage] | List[Dict],
        user_prompt: str | List[BaseMessage] | List[Dict],
        freeze_system_prompt: bool = False,
        freeze_user_prompt: bool = True,
        llm_call_id: str = "llm_call",
        on_parameter_updated: Optional[Callable[[str, Any], None]] = None,
    ) -> None:
        """Initialize LLM call operator.

        Args:
            model_name: Model identifier for LLM invocation
            llm: Model instance for execution
            system_prompt: System message(s) for the LLM
            user_prompt: User prompt template (supports {{query}} substitution)
            freeze_system_prompt: If True, system_prompt is not tunable
            freeze_user_prompt: If True, user_prompt is not tunable
            id: Unique operator identifier
            on_parameter_updated: Optional callback when parameters change
        """
        self._llm = llm
        self._model_name = model_name
        self._system_prompt = PromptTemplate(content=system_prompt)
        self._user_prompt = PromptTemplate(content=user_prompt or DEFAULT_USER_PROMPT)
        self._freeze_system_prompt = freeze_system_prompt
        self._freeze_user_prompt = freeze_user_prompt
        self._llm_call_id = llm_call_id
        self._on_parameter_updated = on_parameter_updated

    @property
    def operator_id(self) -> str:
        """Operator identifier.

        Returns:
            Operator ID string
        """
        return self._llm_call_id

    def get_tunables(self) -> Dict[str, TunableSpec]:
        """Get tunable parameters.

        Returns:
            Dict of tunable names to TunableSpec (system_prompt, user_prompt if not frozen)
        """
        tunables: Dict[str, TunableSpec] = {}
        if not self._freeze_system_prompt:
            tunables["system_prompt"] = TunableSpec(
                name="system_prompt",
                kind="prompt",
                path="system_prompt",
            )
        if not self._freeze_user_prompt:
            tunables["user_prompt"] = TunableSpec(
                name="user_prompt",
                kind="prompt",
                path="user_prompt",
            )
        return tunables

    def set_parameter(self, target: str, value: Any) -> None:
        """Set tunable parameter value.

        Args:
            target: Parameter name (system_prompt or user_prompt)
            value: New prompt content (str or list)
        """
        content = value if isinstance(value, (str, list)) else str(value)
        if target == "system_prompt" and not self._freeze_system_prompt:
            self.update_system_prompt(content)
            if self._on_parameter_updated is not None:
                self._on_parameter_updated("system_prompt", content)
        elif target == "user_prompt" and not self._freeze_user_prompt:
            self.update_user_prompt(content)
            if self._on_parameter_updated is not None:
                self._on_parameter_updated("user_prompt", content)

    def get_state(self) -> Dict[str, Any]:
        """Get current prompt state for checkpoint.

        Returns:
            Dict with system_prompt and user_prompt content
        """
        return {
            "system_prompt": self._system_prompt.content,
            "user_prompt": self._user_prompt.content,
        }

    def load_state(self, state: Dict[str, Any]) -> None:
        """Restore prompt state from checkpoint.

        Args:
            state: State dict with system_prompt and/or user_prompt
        """
        if "system_prompt" in state:
            self.update_system_prompt(state["system_prompt"])
        if "user_prompt" in state:
            self.update_user_prompt(state["user_prompt"])

    async def invoke(
        self,
        inputs: Dict[str, Any],
        session: Session,
        **kwargs: Any,
    ) -> AssistantMessage:
        """Execute LLM invocation.

        Args:
            inputs: Input dict with query and optional messages
            session: Session for tracing
            history: Optional conversation history
            tools: Optional tool definitions for function calling
            **kwargs: Additional parameters (supports history, tools via kwargs for backward compat)

        Returns:
            LLM response message
        """
        # Support both explicit params and kwargs for backward compatibility
        history: Optional[List[BaseMessage]] = kwargs.get("history")
        tools: Optional[List[ToolInfo]] = kwargs.get("tools")
        messages = self._format_messages(inputs, history)
        self._set_operator_context(session, self._llm_call_id)
        try:
            return await self._llm.invoke(model=self._model_name, messages=messages, tools=tools)
        finally:
            self._set_operator_context(session, None)

    async def stream(
        self,
        inputs: Dict[str, Any],
        session: Session,
        **kwargs: Any,
    ) -> AsyncIterator[AssistantMessage]:
        """Stream LLM invocation.

        Args:
            inputs: Input dict with query and optional messages
            session: Session for tracing
            history: Optional conversation history
            tools: Optional tool definitions for function calling
            **kwargs: Additional parameters

        Yields:
            LLM response chunks
        """
        # Support both explicit params and kwargs for backward compatibility
        history: Optional[List[BaseMessage]] = kwargs.get("history")
        tools: Optional[List[ToolInfo]] = kwargs.get("tools")
        messages = self._format_messages(inputs, history)
        self._set_operator_context(session, self._llm_call_id)
        try:
            message_chunks: List[Any] = []
            async for chunk in self._llm.stream(model=self._model_name, messages=messages, tools=tools):
                chunk_content = chunk.content if hasattr(chunk, "content") else str(chunk)
                message_chunks.append(chunk_content)
                yield chunk
        finally:
            self._set_operator_context(session)

    def get_system_prompt(self) -> PromptTemplate:
        """Get system prompt template.

        Returns:
            System PromptTemplate
        """
        return self._system_prompt

    def get_user_prompt(self) -> PromptTemplate:
        """Get user prompt template.

        Returns:
            User PromptTemplate
        """
        return self._user_prompt

    def update_system_prompt(self, system_prompt: str | List[BaseMessage] | List[Dict]) -> None:
        """Update system prompt (if not frozen).

        Args:
            system_prompt: New system prompt content
        """
        if not self._freeze_system_prompt:
            self._system_prompt = PromptTemplate(content=system_prompt)

    def update_user_prompt(self, user_prompt: str | List[BaseMessage] | List[Dict]) -> None:
        """Update user prompt (if not frozen).

        Args:
            user_prompt: New user prompt content
        """
        if not self._freeze_user_prompt:
            self._user_prompt = PromptTemplate(content=user_prompt)

    def set_freeze_system_prompt(self, switch: bool) -> None:
        """Set system prompt freeze state.

        Args:
            switch: True to freeze, False to enable tuning
        """
        self._freeze_system_prompt = switch

    def set_freeze_user_prompt(self, switch: bool) -> None:
        """Set user prompt freeze state.

        Args:
            switch: True to freeze, False to enable tuning
        """
        self._freeze_user_prompt = switch

    def get_freeze_system_prompt(self) -> bool:
        """Get system prompt freeze state.

        Returns:
            True if frozen (not tunable)
        """
        return self._freeze_system_prompt

    def get_freeze_user_prompt(self) -> bool:
        """Get user prompt freeze state.

        Returns:
            True if frozen (not tunable)
        """
        return self._freeze_user_prompt

    def _format_messages(
        self,
        inputs: Dict[str, Any],
        history: Optional[List[BaseMessage]] = None,
    ) -> List[BaseMessage]:
        """Format LLM input messages from inputs dict.

        Args:
            inputs: Input dict with query or messages
            history: Optional conversation history

        Returns:
            Formatted message list
        """
        if isinstance(inputs.get("messages"), list):
            return self._format_passthrough(inputs)
        return self._format_llm_input(inputs, history)

    def _format_llm_input(
        self,
        inputs: Dict[str, Any],
        history: Optional[List[BaseMessage]] = None,
    ) -> List[BaseMessage]:
        """Format messages in traditional mode: system + history + user.

        Args:
            inputs: Input dict with query
            history: Optional conversation history

        Returns:
            Concatenated message list
        """
        system_messages = [
            SystemMessage(content=msg.content)
            for msg in self._system_prompt.format(inputs).to_messages()
        ]
        user_messages = self._user_prompt.format(inputs).to_messages()
        history_messages = history if history is not None else []
        return [*system_messages, *history_messages, *user_messages]

    def _format_passthrough(self, inputs: Dict[str, Any]) -> List[BaseMessage]:
        """Format messages in passthrough mode.

        In this mode, inputs["messages"] contains pre-constructed context window messages
        (typically from ContextEngine), and only system prompt is injected.

        Args:
            inputs: Input dict with messages key

        Returns:
            System prompt + passthrough messages
        """
        system_messages = [
            SystemMessage(content=msg.content)
            for msg in self._system_prompt.format(inputs).to_messages()
        ]
        history_messages = inputs.get("messages") or []
        return [*system_messages, *history_messages]


# Backward compatible alias (legacy name)
LLMCall = LLMCallOperator