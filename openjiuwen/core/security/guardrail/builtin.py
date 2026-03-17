# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Guardrail Framework Builtin Implementations

Provides ready-to-use guardrail implementations for common security scenarios.
Users can use these directly with custom backends or extend them for
additional customization.

Note: Currently only UserInputGuardrail is implemented as an example.
Other guardrails will be added as needed, with event names and data
structures adjusted to align with the actual implementation.
"""

from typing import Any, Dict, List

from openjiuwen.core.security.guardrail.guardrail import BaseGuardrail
from openjiuwen.core.security.guardrail.models import GuardrailResult


class UserInputGuardrail(BaseGuardrail):
    """Guardrail for user input validation.

    Monitors user input events to detect risks like prompt injection,
    jailbreak attempts, or malicious content.

    Default events: user_input

    Example:
        >>> # Use with default events
        >>> guardrail = UserInputGuardrail()
        >>> guardrail.set_backend(PromptInjectionDetector())
        >>> await guardrail.register(callback_framework)
        >>>
        >>> # Use with custom events
        >>> guardrail = UserInputGuardrail(events=["custom_user_input"])

    Note: The event name and data structure (e.g., 'text' field) should
    align with the actual event triggering implementation in the
    agent execution flow.
    """

    DEFAULT_EVENTS = ["user_input"]

    async def detect(self, event_name: str, *args, **kwargs) -> GuardrailResult:
        """Detect risks in user input.

        Args:
            event_name: The name of the triggered event.
            *args: Positional arguments passed from the callback framework
            **kwargs: Keyword arguments (event data) passed from the callback
                framework when the event is triggered, containing:
                - text: The user input text
                - user_id: Optional user identifier
                - session_id: Optional session identifier

        Note: The actual data fields (e.g., 'text') should match the event
        data structure used when triggering events in the agent execution
        flow. Update this method when the event mechanism is finalized.
        """
        text = kwargs.get("text", "")

        if not text or not isinstance(text, str):
            return GuardrailResult.pass_(details={"empty_input": True})

        if self._backend:
            return await super().detect(event_name, *args, **kwargs)

        return GuardrailResult.pass_()
