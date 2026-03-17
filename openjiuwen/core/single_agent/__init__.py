# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Single Agent Module

This module provides exports for single agent functionality.
Legacy implementations are in the legacy/ directory and should be
imported from openjiuwen.core.single_agent.legacy explicitly.

For migration guide, see: docs/AGENT_MIGRATION_GUIDE.md

Note: Legacy classes have been moved to the legacy submodule.
Please use 'from openjiuwen.core.single_agent.legacy import ...' for
legacy classes like LegacyReActAgent, AgentConfig, etc.
"""

import importlib
from typing import TYPE_CHECKING

# New classes (current API)
from openjiuwen.core.session.agent import Session, create_agent_session
from openjiuwen.core.single_agent.base import BaseAgent  # Base class import must come first

from .ability_manager import AbilityManager, AddAbilityResult
from .agents.react_agent import ReActAgent, ReActAgentConfig
from .agents.react_agent_evolve import ReActAgentEvolve
from .schema.agent_card import AgentCard

# Legacy classes (need this import for IDE hinting to work)
if TYPE_CHECKING:
    from openjiuwen.core.single_agent.legacy import LegacyBaseAgent

__all__ = [
    # New classes
    "AgentCard",
    "ReActAgent",
    "ReActAgentConfig",
    "ReActAgentEvolve",
    "Session",
    "create_agent_session",
    "BaseAgent",
    "AbilityManager",
    # For compatibility
    "LegacyBaseAgent",
    "AddAbilityResult"
]


def __getattr__(name: str):
    """
    Lazy import for deprecated modules using PEP 562.
    """
    if name == "LegacyBaseAgent":
        from openjiuwen.core.single_agent.legacy import LegacyBaseAgent

        return LegacyBaseAgent
    return importlib.import_module("." + name, __name__)
