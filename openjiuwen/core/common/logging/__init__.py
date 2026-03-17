# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Logging Module

Provides a modular logging system supporting independent logging for each module in the project.
Each module has its dedicated logger for easy log management and debugging.
"""

from typing import (
    Any,
    Callable,
    Optional,
)

from openjiuwen.core.common.logging.events import (
    AgentEvent,
    BaseLogEvent,
    ContextEvent,
    create_log_event,
    EventStatus,
    LLMEvent,
    LogEventType,
    LogLevel,
    MemoryEvent,
    ModuleType,
    PerformanceEvent,
    RetrievalEvent,
    sanitize_event_for_logging,
    SessionEvent,
    SystemEvent,
    ToolEvent,
    UserInteractionEvent,
    validate_event,
    WorkflowEvent,
)
from openjiuwen.core.common.logging.manager import LogManager
from openjiuwen.core.common.logging.protocol import LoggerProtocol
from openjiuwen.core.common.logging.utils import (
    get_session_id,
    set_session_id,
)

_initialized = False


def _ensure_initialized():
    """Ensure the logging system is initialized"""
    global _initialized
    if not _initialized:
        try:
            from openjiuwen.core.common.logging.default.default_impl import DefaultLogger

            LogManager.set_default_logger_class(DefaultLogger)
        except ImportError:
            pass
        LogManager.initialize()
        _initialized = True


class LazyLogger:
    """
    Lazy initialization logger

    Logger is only initialized when actually used, improving startup performance.
    Suitable for module-level logger definitions.
    """

    def __init__(self, getter_func: Callable[[], LoggerProtocol]) -> None:
        """
        Initialize lazy logger

        Args:
            getter_func: Function to get logger, called on first use
        """
        self._getter_func: Callable[[], LoggerProtocol] = getter_func
        self._logger: Optional[LoggerProtocol] = None

    def __getattr__(self, name: str) -> Any:
        """
        Get logger attribute

        Logger is initialized on first access.

        Args:
            name: Attribute name (log method name, e.g., info, debug, etc.)

        Returns:
            Logger method or attribute
        """
        if self._logger is None:
            _ensure_initialized()
            self._logger = self._getter_func()
        return getattr(self._logger, name)


# ========== General Loggers ==========

# General logger - for general log records
logger = LazyLogger(lambda: LogManager.get_logger("common"))

# Interface logger - for interface call logs
interface_logger = LazyLogger(lambda: LogManager.get_logger("interface"))

# Performance logger - for performance metrics
performance_logger = LazyLogger(lambda: LogManager.get_logger("performance"))

# Prompt builder logger - for prompt building related logs
prompt_builder_logger = LazyLogger(lambda: LogManager.get_logger("prompt_builder"))

# ========== Core Module Loggers ==========

# Agent module logger - for single Agent related logs
agent_logger = LazyLogger(lambda: LogManager.get_logger("agent"))

# Multi-Agent module logger - for multi-Agent collaboration related logs
multi_agent_logger = LazyLogger(lambda: LogManager.get_logger("multi_agent"))

# Workflow module logger - for workflow execution related logs
workflow_logger = LazyLogger(lambda: LogManager.get_logger("workflow"))

# Session module logger - for session management related logs
session_logger = LazyLogger(lambda: LogManager.get_logger("session"))

# Controller module logger - for controller related logs
controller_logger = LazyLogger(lambda: LogManager.get_logger("controller"))

# Runner module logger - for executor related logs
runner_logger = LazyLogger(lambda: LogManager.get_logger("runner"))

# SysOperation module logger - for sys_operation related logs
sys_operation_logger = LazyLogger(lambda: LogManager.get_logger("sys_operation"))

# ========== Foundation Module Loggers ==========

# LLM module logger - for LLM call related logs
llm_logger = LazyLogger(lambda: LogManager.get_logger("llm"))

# Tool module logger - for tool call related logs
tool_logger = LazyLogger(lambda: LogManager.get_logger("tool"))

# Prompt module logger - for prompt processing related logs
prompt_logger = LazyLogger(lambda: LogManager.get_logger("prompt"))

# Store module logger - for data store related logs
store_logger = LazyLogger(lambda: LogManager.get_logger("store"))

# ========== Data and Retrieval Module Loggers ==========

# Memory module logger - for memory management related logs
memory_logger = LazyLogger(lambda: LogManager.get_logger("memory"))

# Retrieval module logger - for retrieval related logs
retrieval_logger = LazyLogger(lambda: LogManager.get_logger("retrieval"))

# Context Engine module logger - for context engine related logs
context_engine_logger = LazyLogger(lambda: LogManager.get_logger("context_engine"))

# ========== Execution and Graph Module Loggers ==========

# Graph module logger - for graph execution related logs
graph_logger = LazyLogger(lambda: LogManager.get_logger("graph"))

# Operator module logger - for operator execution related logs
operator_logger = LazyLogger(lambda: LogManager.get_logger("operator"))

# ========== Protocol and Extension Module Loggers ==========

# MCP protocol logger - for MCP protocol related logs
mcp_logger = LazyLogger(lambda: LogManager.get_logger("mcp"))


__all__ = [
    # Protocol and base classes
    "LoggerProtocol",
    "LogManager",
    "set_session_id",
    "get_session_id",
    # General loggers
    "logger",
    "interface_logger",
    "performance_logger",
    "prompt_builder_logger",
    # Core module loggers
    "agent_logger",
    "multi_agent_logger",
    "workflow_logger",
    "session_logger",
    "controller_logger",
    "runner_logger",
    "sys_operation_logger",
    # Foundation module loggers
    "llm_logger",
    "tool_logger",
    "prompt_logger",
    "store_logger",
    # Data and retrieval module loggers
    "memory_logger",
    "retrieval_logger",
    "context_engine_logger",
    # Execution and graph module loggers
    "graph_logger",
    "operator_logger",
    # Protocol and extension module loggers
    "mcp_logger",
    # Event definitions
    "LogEventType",
    "LogLevel",
    "ModuleType",
    "EventStatus",
    "BaseLogEvent",
    "AgentEvent",
    "WorkflowEvent",
    "LLMEvent",
    "ToolEvent",
    "MemoryEvent",
    "SessionEvent",
    "ContextEvent",
    "RetrievalEvent",
    "PerformanceEvent",
    "UserInteractionEvent",
    "SystemEvent",
    "create_log_event",
    "validate_event",
    "sanitize_event_for_logging",
]
