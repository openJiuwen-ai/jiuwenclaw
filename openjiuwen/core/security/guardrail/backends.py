# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Guardrail Framework Backend Interface

Defines the interface for pluggable guardrail detection backends.
Users implement this interface to provide custom detection logic.
"""

from abc import (
    ABC,
    abstractmethod,
)
from typing import (
    Any,
    Dict,
)

from openjiuwen.core.security.guardrail.models import RiskAssessment


class GuardrailBackend(ABC):
    """Abstract base class for guardrail detection backends.

    A guardrail backend implements the actual detection logic for a specific
    type of risk (e.g., prompt injection, sensitive data leakage).
    Users can implement custom backends by inheriting from this class and
    providing their detection algorithms.

    Example:
        >>> class MyPromptInjectionBackend(GuardrailBackend):
        ...     async def analyze(self, data):
        ...         text = data.get("text", "")
        ...         has_risk = self._detect_injection(text)
        ...         return RiskAssessment(
        ...             has_risk=has_risk,
        ...             risk_level=RiskLevel.HIGH if has_risk else RiskLevel.SAFE,
        ...             risk_type="prompt_injection"
        ...         )
    """

    @abstractmethod
    async def analyze(self, data: Dict[str, Any]) -> RiskAssessment:
        """Analyze data for security risks.

        This method implements the core detection logic. It receives the event
        data and returns a risk assessment. All necessary information for
        detection must be contained in the data parameter.

        Args:
            data: The event data to analyze. Contents vary by event type:
                - user_input: {"text": "..."}
                - llm_input: {"prompt": "...", "messages": [...]}
                - llm_output: {"content": "...", "tool_calls": [...]}
                - tool_input: {"tool_name": "...", "tool_input": {...}}
                - tool_output: {"tool_name": "...", "tool_output": {...}}

        Returns:
            RiskAssessment describing detected risks.

        Raises:
            Any exception will be caught by the guardrail framework and
            result in a failed detection (conservative approach).
        """
        pass
