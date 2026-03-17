# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Legacy Interface Compatibility Layer

This package contains all deprecated interfaces for backward compatibility.
All interfaces in this package will be removed in v1.0.0.

For migration guide, see: docs/AGENT_MIGRATION_GUIDE.md

Created on: 2025-01-04

Note: This module uses explicit imports to enable IDE navigation (Go to Definition).
Deprecation warnings are issued when legacy classes are instantiated.
"""
import warnings
from functools import wraps


def _deprecated_class(cls, alternative: str):
    """Decorator to add deprecation warning on class instantiation.

    Args:
        cls: The class to wrap
        alternative: Description of the recommended alternative

    Returns:
        The wrapped class with deprecation warning on __init__
    """
    # Check if already decorated to avoid double warnings
    if getattr(cls, 'deprecated_wrapped', False):
        return cls

    original_init = cls.__init__

    @wraps(original_init)
    def new_init(self, *args, **kwargs):
        warnings.warn(
            f"{cls.__name__} is deprecated and will be removed in the "
            f"future. Please use {alternative} instead.",
            DeprecationWarning,
            stacklevel=2
        )
        original_init(self, *args, **kwargs)

    cls.__init__ = new_init
    cls.deprecated_wrapped = True
    return cls


# ===== Legacy imports (explicit for IDE navigation) =====

# From legacy.agent
from openjiuwen.core.single_agent.legacy.agent import (
    BaseAgent as _BaseAgent,
    ControllerAgent as _ControllerAgent,
    WorkflowFactory as _WorkflowFactory,
    workflow_provider,
)


# From legacy.config
from openjiuwen.core.single_agent.legacy.config import (
    AgentConfig as _AgentConfig,
    LLMCallConfig as _LLMCallConfig,
    IntentDetectionConfig as _IntentDetectionConfig,
    ConstrainConfig as _ConstrainConfig,
    DefaultResponse as _DefaultResponse,
    WorkflowAgentConfig as _WorkflowAgentConfig,
    LegacyReActAgentConfig as _LegacyReActAgentConfig,
    MemoryConfig as _MemoryConfig,
)

# From legacy.schema
from openjiuwen.core.single_agent.legacy.schema import (
    WorkflowSchema as _WorkflowSchema,
    PluginSchema as _PluginSchema,
)

# From legacy.react_agent
from openjiuwen.core.single_agent.legacy.react_agent import (
    AgentSession, LegacyReActAgent as _LegacyReActAgent,
    create_react_agent_config,
)


# ===== Apply deprecation warnings to legacy classes =====

LegacyBaseAgent = _deprecated_class(
    _BaseAgent,
    "openjiuwen.core.single_agent.agent.BaseAgent"
)
ControllerAgent = _deprecated_class(
    _ControllerAgent,
    "openjiuwen.core.single_agent.agent.BaseAgent"
)

WorkflowFactory = _deprecated_class(
    _WorkflowFactory,
    "Workflow class directly"
)

AgentConfig = _deprecated_class(
    _AgentConfig,
    "AgentCard + ReActAgentConfig"
)
LLMCallConfig = _deprecated_class(
    _LLMCallConfig,
    "ReActAgentConfig"
)
IntentDetectionConfig = _deprecated_class(
    _IntentDetectionConfig,
    "new config classes"
)
ConstrainConfig = _deprecated_class(
    _ConstrainConfig,
    "ReActAgentConfig"
)
DefaultResponse = _deprecated_class(
    _DefaultResponse,
    "new config classes"
)
WorkflowAgentConfig = _deprecated_class(
    _WorkflowAgentConfig,
    "WorkflowAgentConfig from workflow module"
)
MemoryConfig = _deprecated_class(
    _MemoryConfig,
    "MemoryScopeConfig"
)
LegacyReActAgentConfig = _deprecated_class(
    _LegacyReActAgentConfig,
    "openjiuwen.core.single_agent.agents.react_agent.ReActAgentConfig"
)

LegacyReActAgent = _deprecated_class(
    _LegacyReActAgent,
    "openjiuwen.core.single_agent.agents.react_agent.ReActAgent"
)

WorkflowSchema = _deprecated_class(
    _WorkflowSchema,
    "WorkflowCard"
)
PluginSchema = _deprecated_class(
    _PluginSchema,
    "Tool class directly"
)


__all__ = [
    # Legacy agents
    "LegacyReActAgent",
    "create_react_agent_config",
    # Legacy base classes
    "LegacyBaseAgent",
    "ControllerAgent",
    "AgentSession",
    "WorkflowFactory",
    "workflow_provider",
    # Legacy configs
    "AgentConfig",
    "LLMCallConfig",
    "IntentDetectionConfig",
    "ConstrainConfig",
    "DefaultResponse",
    "WorkflowAgentConfig",
    "MemoryConfig",
    "LegacyReActAgentConfig",
    # Legacy schemas
    "WorkflowSchema",
    "PluginSchema",
]
