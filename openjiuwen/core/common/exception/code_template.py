# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025.

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    Optional,
    Set,
)

ALLOWED_SCOPES = {
    "WORKFLOW",
    "COMPONENT",
    "AGENT",
    "TOOL",
    "MODEL",
    "SESSION",
    "GRAPH",
    "CONTROLLER",
    "RUNNER",
    "PROMPT",
    "COMMON",
    "CONTEXT",
    "TOOLCHAIN",
    "MEMORY",
    "RETRIEVAL",
    "SYS_OPERATION"
}

ALLOWED_FAILURE_TYPES = {
    # Validation
    "INVALID",
    "NOT_FOUND",
    "NOT_SUPPORTED",
    "CONFIG_ERROR",
    "PARAM_ERROR",
    "TYPE_ERROR",

    # Framework
    "INIT_FAILED",
    "CALL_FAILED",

    # Execution
    "EXECUTION_ERROR",
    "RUNTIME_ERROR",
    "PROCESS_ERROR",
    "TIMEOUT",
    "INTERRUPTED",
}


@dataclass(frozen=True)
class StatusCodeTemplate:
    name: str
    code_suggestion: str
    message_template: str
    exception_semantic: str


def _exception_semantic_from_failure(failure_type: str) -> str:
    if failure_type in {"INVALID", "NOT_FOUND", "NOT_SUPPORTED", "CONFIG_ERROR", "PARAM_ERROR"}:
        return "ValidationError"
    if failure_type in {"INIT_FAILED", "CALL_FAILED"}:
        return "FrameworkError"
    return "ExecutionError"


def _code_range_by_scope(scope: str) -> str:
    return {
        "WORKFLOW": "100000–100999",
        "COMPONENT": "101000–119999",
        "AGENT": "120000–129999",
        "RUNNER": "130000–139999",
        "GRAPH": "140000–149999",
        "CONTEXT": "150000–154999",
        "RETRIEVAL": "155000-157999",
        "MEMORY": "158000-159999",
        "TOOLCHAIN": "160000–179999",
        "PROMPT": "180000-180999",
        "MODEL": "181000–181999",
        "TOOL": "182000-182999",
        "COMMON": "188000-188999",
        "SESSION": "190000–198999",
        "SYS_OPERATION": "199000–199999",
    }.get(scope, "custom")


def generate_status_code(
    *,
    scope: str,
    subject: str,
    failure_type: str,
    detail: Optional[str] = None,
) -> StatusCodeTemplate:
    # ---------- validation ----------
    _validate(scope, failure_type)

    # ---------- name generation ----------
    name = _gen_name(scope, subject, detail, failure_type)

    # ---------- message template ----------
    message_template = generate_error_message_template(
        scope=scope,
        subject=subject,
        failure_type=failure_type,
    )

    return StatusCodeTemplate(
        name=name,
        code_suggestion=_code_range_by_scope(scope),
        message_template=message_template.template,
        exception_semantic=_exception_semantic_from_failure(failure_type),
    )


def _gen_name(scope: str, subject: str, detail: Optional[str], failure_type: str) -> str:
    parts = [scope]

    if detail:
        parts.append(detail)

    parts.append(subject)
    parts.append(failure_type)

    name = "_".join(parts)
    return name


def _validate(scope: str, failure_type: str):
    if scope not in ALLOWED_SCOPES:
        raise ValueError(f"Invalid scope: {scope}")

    if failure_type not in ALLOWED_FAILURE_TYPES:
        raise ValueError(f"Invalid failure type: {failure_type}")


@dataclass(frozen=True)
class ErrorMessageTemplate:
    template: str
    params: Set[str]


def generate_error_message_template(
    *,
    scope: str,
    subject: str,
    failure_type: str,
    with_reason: bool = True,
) -> ErrorMessageTemplate:
    scope = scope.lower()
    subject = subject.lower()

    params: Set[str] = set()

    # ---------- base sentence ----------
    if failure_type == "INVALID":
        msg = f"{scope} {subject} is invalid"
    elif failure_type == "PARAM_ERROR":
        msg = f"{scope} {subject} parameter error"
    elif failure_type == "NOT_FOUND":
        msg = f"{scope} {subject} not found"
    elif failure_type in ("NOT_SUPPORT", "NOT_SUPPORTED"):
        msg = f"{scope} {subject} is not supported"
    elif failure_type == "CONFIG_ERROR":
        msg = f"{scope} {subject} config error"
    elif failure_type == "INIT_FAILED":
        msg = f"{scope} {subject} initialization failed"
    elif failure_type == "CALL_FAILED":
        msg = f"{scope} {subject} call failed"
    elif failure_type == "EXECUTION_ERROR":
        msg = f"{scope} {subject} execution error"
    elif failure_type == "RUNTIME_ERROR":
        msg = f"{scope} {subject} runtime error"
    elif failure_type == "PROCESS_ERROR":
        msg = f"{scope} {subject} process error"
    elif failure_type == "TIMEOUT":
        msg = f"{scope} {subject} timeout"
        params.add("timeout")
        msg += " ({timeout}s)"
    elif failure_type == "INTERRUPTED":
        msg = f"{scope} {subject} interrupted"
    else:
        raise ValueError(f"Unsupported failure type: {failure_type}")

    # ---------- optional reason ----------
    if with_reason:
        params.add("error_msg")
        msg += ", reason: {error_msg}"

    return ErrorMessageTemplate(
        template=msg,
        params=params,
    )


@dataclass(frozen=True)
class StatusCodeSpec:
    name: str
    code: int
    message: str


def generate_status_code_spec(
    *,
    template: StatusCodeTemplate,
    code: int,
) -> StatusCodeSpec:
    name = template.name
    msg = template.message_template
    return StatusCodeSpec(
        name=name,
        code=code,
        message=msg,
    )


def render_enum_member(spec: StatusCodeSpec) -> str:
    return f'    {spec.name} = ({spec.code}, "{spec.message}")'
