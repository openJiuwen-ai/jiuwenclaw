# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Guardrail Framework Builtin Implementations

Provides ready-to-use guardrail implementations for common security scenarios.
Users can use these directly with custom backends or extend them for
additional customization.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openjiuwen.core.security.guardrail.guardrail import BaseGuardrail
from openjiuwen.core.security.guardrail.context import (
    GuardrailContext,
    GuardrailContentType,
    ModelOutputParser,
    BertBinaryParser,
    QwenGuardParser,
)
from openjiuwen.core.security.guardrail.backends import (
    GuardrailBackend,
    RuleBasedPromptInjectionBackend,
    RuleBasedBackendConfig,
    APIModelBackend,
    APIModelBackendConfig,
    LocalModelBackend,
    LocalModelBackendConfig,
)
from openjiuwen.core.security.guardrail.enums import RiskLevel
from openjiuwen.core.runner.callback.events import (
    LLMCallEvents,
    ToolCallEvents,
)


@dataclass
class PromptInjectionGuardrailConfig:
    """Configuration for PromptInjectionGuardrail.

    Groups related parameters to reduce the number of constructor arguments.
    """
    mode: str = "rules"
    model_type: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    timeout: float = 30.0
    model_path: Optional[str] = None
    device: str = "auto"
    custom_patterns: Optional[List[str]] = None
    risk_level: RiskLevel = RiskLevel.HIGH
    bert_thresholds: Optional[Dict[str, float]] = None
    attack_class_id: int = 1
    qwen_risk_type: str = "content_risk"
    parser: Optional[ModelOutputParser] = None


class PromptInjectionGuardrail(BaseGuardrail):
    """Prompt injection detection guardrail.

    Monitors LLM input and tool output for prompt injection attacks.

    Default events:
        - LLMCallEvents.LLM_INVOKE_INPUT: Check content going into LLM
        - ToolCallEvents.TOOL_INVOKE_OUTPUT: Check content returned from tools

    Example (with built-in rule-based backend):
        >>> # Use with default rules
        >>> guardrail = PromptInjectionGuardrail()
        >>> await register_guardrail(guardrail)

        >>> # Use with custom rules
        >>> config = PromptInjectionGuardrailConfig(
        ...     custom_patterns=[r"your_pattern"],
        ...     risk_level=RiskLevel.CRITICAL
        ... )
        >>> guardrail = PromptInjectionGuardrail(config=config)

    Example (with API model backend):
        >>> config = PromptInjectionGuardrailConfig(
        ...     mode="api",
        ...     model_type="bert",
        ...     api_url="https://api.example.com/detect",
        ...     api_key="your-key"
        ... )
        >>> guardrail = PromptInjectionGuardrail(config=config)

    Example (with local model backend):
        >>> config = PromptInjectionGuardrailConfig(
        ...     mode="local",
        ...     model_type="qwen",
        ...     model_path="/path/to/model"
        ... )
        >>> guardrail = PromptInjectionGuardrail(config=config)

    Example (with custom backend):
        >>> backend = MyCustomBackend()
        >>> guardrail = PromptInjectionGuardrail(backend=backend)
    """

    DEFAULT_EVENTS: List[Any] = [
        LLMCallEvents.LLM_INVOKE_INPUT,
        ToolCallEvents.TOOL_INVOKE_OUTPUT,
    ]

    def __init__(
        self,
        *,
        events: Optional[List[Any]] = None,
        backend: Optional[GuardrailBackend] = None,
        priority: Optional[int] = None,
        enable_logging: bool = True,
        config: Optional[PromptInjectionGuardrailConfig] = None,
        **kwargs
    ):
        """Initialize prompt injection guardrail.

        Args:
            events: Optional custom event list
            backend: Optional detection backend. If provided, config is ignored.
            priority: Optional priority for callback registration
            enable_logging: Enable logging output
            config: Configuration dataclass (preferred over individual params).
            **kwargs: Additional arguments passed to BaseGuardrail
        """
        if backend is not None:
            final_backend = backend
        elif config is not None:
            final_backend = self._build_backend_from_config(config)
        else:
            final_backend = RuleBasedPromptInjectionBackend()

        super().__init__(
            events=events,
            backend=final_backend,
            priority=priority,
            enable_logging=enable_logging,
            **kwargs
        )

    @staticmethod
    def _build_backend_from_config(
        config: PromptInjectionGuardrailConfig
    ) -> GuardrailBackend:
        """Build backend from configuration dataclass."""
        mode = config.mode

        if mode not in ("rules", "api", "local"):
            raise ValueError(
                f"invalid mode: {mode}, must be 'rules', 'api' or 'local'"
            )

        if mode == "rules":
            return RuleBasedPromptInjectionBackend(
                RuleBasedBackendConfig(
                    patterns=config.custom_patterns,
                    risk_level=config.risk_level
                )
            )

        if mode == "api" and not config.api_url:
            raise ValueError("api_url is required for api mode")

        if mode == "local" and not config.model_path:
            raise ValueError("model_path is required for local mode")

        if config.model_type is None and config.parser is None:
            raise ValueError(
                "either model_type or parser must be specified for api/local mode"
            )

        if config.model_type not in (None, "bert", "qwen"):
            raise ValueError(f"unknown model_type: {config.model_type}")

        parser = config.parser
        if parser is None:
            if config.model_type == "bert":
                parser = BertBinaryParser(
                    risk_type="prompt_injection",
                    confidence_thresholds=config.bert_thresholds,
                    attack_class_id=config.attack_class_id,
                )
            else:
                parser = QwenGuardParser(risk_type=config.qwen_risk_type)

        if mode == "api":
            return APIModelBackend(
                APIModelBackendConfig(
                    api_url=config.api_url,
                    parser=parser,
                    api_key=config.api_key,
                    timeout=config.timeout,
                )
            )

        return LocalModelBackend(
            LocalModelBackendConfig(
                model_path=config.model_path,
                parser=parser,
                device=config.device,
            )
        )

    @staticmethod
    def _normalize_event(event: Any) -> str:
        """Normalize event to string format for comparison.

        Args:
            event: Event object or string

        Returns:
            Normalized event string
        """
        if isinstance(event, str):
            return event
        return str(event)

    def extract_context(
        self,
        event: Any,
        *args,
        **kwargs
    ) -> GuardrailContext:
        """Extract context from different event types.

        Handles:
        - LLMCallEvents.LLM_INVOKE_INPUT: Extracts from messages/kwargs
        - ToolCallEvents.TOOL_INVOKE_OUTPUT: Extracts from tool result

        Supports both event objects and string event names.

        Args:
            event: The triggered event (object or string)
            *args: Positional arguments from callback framework
            **kwargs: Keyword arguments from callback framework

        Returns:
            GuardrailContext with unified data format
        """
        metadata = {"event_source": str(event)}
        event_str = self._normalize_event(event)
        llm_input_str = self._normalize_event(LLMCallEvents.LLM_INVOKE_INPUT)
        tool_output_str = self._normalize_event(ToolCallEvents.TOOL_INVOKE_OUTPUT)

        if event_str == llm_input_str or event == LLMCallEvents.LLM_INVOKE_INPUT:
            messages = kwargs.get("messages", [])
            if messages:
                last_msg = messages[-1]
                text = self._extract_text_from_message(last_msg)
                metadata["message_count"] = len(messages)
                return GuardrailContext(
                    content_type=GuardrailContentType.TEXT,
                    content=text,
                    event=str(event),
                    metadata=metadata
                )
            return GuardrailContext(
                content_type=GuardrailContentType.MESSAGES,
                content=messages,
                event=str(event),
                metadata=metadata
            )

        elif event_str == tool_output_str or event == ToolCallEvents.TOOL_INVOKE_OUTPUT:
            result = kwargs.get("result")
            text = str(result) if result is not None else ""
            return GuardrailContext(
                content_type=GuardrailContentType.TEXT,
                content=text,
                event=str(event),
                metadata=metadata
            )

        return GuardrailContext(
            content_type=GuardrailContentType.RAW,
            content={"args": args, "kwargs": kwargs},
            event=str(event),
            metadata=metadata
        )

    def _extract_text_from_message(self, message: Any) -> str:
        """Extract text content from a message object.

        Tries multiple ways to get text from different message formats.

        Args:
            message: Message object (could be dict, dataclass, etc.)

        Returns:
            Extracted text as string
        """
        # Try dict format first
        if isinstance(message, dict):
            return message.get("content", str(message))

        # Try attribute access
        try:
            content = getattr(message, "content", None)
            if content is not None:
                return str(content)
        except (AttributeError, TypeError):
            pass

        # Fallback to string representation
        return str(message)
