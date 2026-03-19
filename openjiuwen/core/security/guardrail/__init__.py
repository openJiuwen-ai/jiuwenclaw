# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Guardrail Framework

Security detection and interception framework for agent execution.
Integrates with the callback system to detect risks at critical execution points.

Usage (Recommended - Simple with Rules):
    from openjiuwen.core.security.guardrail import (
        PromptInjectionGuardrail,
        register_guardrail,
    )

    # Use built-in guardrail with pre-configured rules
    guardrail = PromptInjectionGuardrail.with_rules()
    await register_guardrail(guardrail)

    # Or with custom rules
    guardrail = PromptInjectionGuardrail.with_rules(
        custom_patterns=[r"your_pattern"]
    )

Usage (With LLM Backend):
    from openjiuwen.core.security.guardrail import (
        PromptInjectionGuardrail,
        LLMPromptInjectionBackend,
        LLMPromptInjectionBackendConfig,
        register_guardrail,
    )

    # Configure LLM backend
    config = LLMPromptInjectionBackendConfig(
        api_endpoint="https://api.example.com/guardrail",
        api_key="your-api-key",
        model_name="guardrail-detector-v1"
    )
    backend = LLMPromptInjectionBackend(config)

    # Create and register guardrail
    guardrail = PromptInjectionGuardrail(backend=backend)
    await register_guardrail(guardrail)

Usage (Advanced - Direct Registration):
    from openjiuwen.core.security.guardrail import PromptInjectionGuardrail
    from openjiuwen.core.runner import Runner

    # If you need direct access to the framework
    guardrail = PromptInjectionGuardrail.with_rules()
    await guardrail.register(Runner.callback_framework)
"""

from openjiuwen.core.security.guardrail.enums import (
    RiskLevel,
    GuardrailContentType,
)

# Data Models
from openjiuwen.core.security.guardrail.models import (
    GuardrailResult,
    RiskAssessment,
)

# Context and Parsers
from openjiuwen.core.security.guardrail.context import (
    GuardrailContext,
    ModelOutputParser,
    BertBinaryParser,
    QwenGuardParser,
)

# Base Classes and Backends
from openjiuwen.core.security.guardrail.backends import (
    GuardrailBackend,
    RuleBasedPromptInjectionBackend,
    RuleBasedBackendConfig,
    LLMPromptInjectionBackend,
    LLMPromptInjectionBackendConfig,
    APIModelBackend,
    APIModelBackendConfig,
    LocalModelBackend,
    LocalModelBackendConfig,
)
from openjiuwen.core.security.guardrail.guardrail import BaseGuardrail

# Builtin Guardrails
from openjiuwen.core.security.guardrail.builtin import (
    PromptInjectionGuardrail,
    PromptInjectionGuardrailConfig,
)

# Exceptions
from openjiuwen.core.common.exception.errors import GuardrailError

from openjiuwen.core.common.logging import logger

# Global reference to Runner (lazy import)
_Runner = None


def _get_runner():
    """Lazy import Runner to avoid circular imports."""
    global _Runner
    if _Runner is None:
        from openjiuwen.core.runner import Runner
        _Runner = Runner
    return _Runner


async def register_guardrail(guardrail: BaseGuardrail) -> None:
    """Convenience function to register a guardrail with the global callback framework.

    This is the recommended way to register guardrails. It gets the global
    callback framework from Runner and registers the guardrail.

    Args:
        guardrail: The guardrail instance to register.

    Example:
        >>> guardrail = PromptInjectionGuardrail.with_rules()
        >>> await register_guardrail(guardrail)
    """
    runner = _get_runner()
    framework = runner.callback_framework
    await guardrail.register(framework)
    logger.info(f"Registered guardrail: {guardrail.__class__.__name__}")


async def unregister_guardrail(guardrail: BaseGuardrail) -> None:
    """Convenience function to unregister a guardrail.

    Args:
        guardrail: The guardrail instance to unregister.
    """
    await guardrail.unregister()
    logger.info(f"Unregistered guardrail: {guardrail.__class__.__name__}")


__all__ = [
    # Enumerations
    "RiskLevel",
    "GuardrailContentType",
    # Data Models
    "GuardrailResult",
    "RiskAssessment",
    # Context and Parsers
    "GuardrailContext",
    "ModelOutputParser",
    "BertBinaryParser",
    "QwenGuardParser",
    # Base Classes and Backends
    "GuardrailBackend",
    "RuleBasedPromptInjectionBackend",
    "RuleBasedBackendConfig",
    "LLMPromptInjectionBackend",
    "LLMPromptInjectionBackendConfig",
    "APIModelBackend",
    "APIModelBackendConfig",
    "LocalModelBackend",
    "LocalModelBackendConfig",
    "BaseGuardrail",
    # Builtin Guardrails
    "PromptInjectionGuardrail",
    "PromptInjectionGuardrailConfig",
    # Exceptions
    "GuardrailError",
    # Convenience Functions
    "register_guardrail",
    "unregister_guardrail",
]
