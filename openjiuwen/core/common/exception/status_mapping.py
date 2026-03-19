# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025.

from __future__ import annotations

from typing import (
    Dict,
    Type,
)

from openjiuwen.core.common.exception.codes import StatusCode


def _get_exception_class_registry() -> Dict[str, Type]:
    # Note: BaseError is imported lazily inside the function to avoid circular imports.
    """
    Lazily import and return the registry mapping exception class names to actual classes.

    This avoids a circular import between `status_mapping` and `errors` at module import time.
    """
    from openjiuwen.core.common.exception import errors as _errors

    return {
        "BaseError": _errors.BaseError,
        "FrameworkError": _errors.FrameworkError,
        "ExecutionError": _errors.ExecutionError,
        "ValidationError": _errors.ValidationError,
        "Termination": _errors.Termination,
        "WorkflowError": _errors.WorkflowError,
        "AgentError": _errors.AgentError,
        "ToolError": _errors.ToolError,
        "GraphError": _errors.GraphError,
        "SessionError": _errors.SessionError,
        "SysOperationError": _errors.SysOperationError,
        "ToolchainError": _errors.ToolchainError,
        "ContextError": _errors.ContextError,
        "RunnerError": _errors.RunnerError,
    }


KEYWORD_RULES = [
    (("INVALID", "VALIDATE", "NOT_SUPPORTED", "PARAM", "MISSING", "DUPLICATED"), "ValidationError"),
    (("CONFIG", "SCHEMA", "FORMAT", "TEMPLATE"), "ValidationError"),

    (("INIT", "CONNECT", "SERVICE", "QUEUE", "PROVIDER"), "FrameworkError"),
    (("CALL", "INVOKE_LLM", "MODEL", "REMOTE"), "FrameworkError"),

    (("TIMEOUT", "EXECUTE", "EXECUTION", "RUNTIME", "PROCESS", "STREAM", "RESPONSE"), "ExecutionError"),
]

RANGE_RULES = [
    ((100000, 119999), "WorkflowError"),
    ((120000, 129999), "AgentError"),
    ((130000, 139999), "RunnerError"),
    ((140000, 149999), "GraphError"),
    ((150000, 159999), "ContextError"),
    ((160000, 179999), "ToolchainError"),
    ((180000, 189999), "FrameworkError"),
    ((190000, 198999), "SessionError"),
    ((199000, 199999), "SysOperationError"),
]

# Manual overrides expressed as names to avoid failing import when some legacy names are absent.
_MANUAL_OVERRIDES_RAW = {
    "CONTROLLER_INVOKE_LLM_FAILED": "FrameworkError",
    "TOOL_EXECUTION_ERROR": "ToolError",
    "TOOL_NOT_FOUND_ERROR": "ValidationError",
    "AGENT_GROUP_EXECUTION_ERROR": "AgentError",
    "STORE_GRAPH_BACKEND_ALREADY_EXISTS": "ValidationError",
    "STORE_GRAPH_PROTOCOL_NOT_IMPLEMENTED": "ValidationError",
    "STORE_GRAPH_BACKEND_NOT_FOUND": "ValidationError",
    "AGENT_RL_PROXY_SERVER_START_FAILED": "FrameworkError",
    "AGENT_RL_PROCESSOR_NOT_FOUND": "ValidationError",
    "AGENT_RL_REWARD_NOT_FOUND": "ValidationError",
}

# Build the actual mapping only for StatusCode members that exist in the current enum.
MANUAL_OVERRIDES: Dict[StatusCode, str] = {}
for _name, _exc in _MANUAL_OVERRIDES_RAW.items():
    if hasattr(StatusCode, _name):
        MANUAL_OVERRIDES[getattr(StatusCode, _name)] = _exc


def _match_keyword(name: str) -> str | None:
    for keywords, exc_name in KEYWORD_RULES:
        if any(k in name for k in keywords):
            return exc_name
    return None


def _match_range(code: int) -> str | None:
    for (start, end), exc_name in RANGE_RULES:
        if start <= code <= end:
            return exc_name
    return None


def resolve_exception_class(status: StatusCode) -> Type:
    # Defer obtaining the actual exception classes to avoid circular import
    registry = _get_exception_class_registry()

    # 1. Manual override
    if status in MANUAL_OVERRIDES:
        return registry[MANUAL_OVERRIDES[status]]

    name = status.name
    code = status.code

    # 2. Keyword rule
    exc_name = _match_keyword(name)
    if exc_name:
        return registry[exc_name]

    # 3. Range fallback
    exc_name = _match_range(code)
    if exc_name:
        return registry[exc_name]

    # 4. Absolute fallback
    return registry["ExecutionError"]


def build_status_exception_map() -> Dict[StatusCode, Type]:
    """
    Generate full StatusCode -> ExceptionClass mapping.
    """
    mapping: Dict[StatusCode, Type] = {}
    for status in StatusCode:
        mapping[status] = resolve_exception_class(status)
    return mapping
