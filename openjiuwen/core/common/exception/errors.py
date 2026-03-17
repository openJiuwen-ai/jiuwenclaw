# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025.

from __future__ import annotations

import json
from copy import deepcopy
from typing import (
    Optional,
    Any,
    Dict,
    Mapping,
)

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.status_mapping import build_status_exception_map


class BaseError(Exception):
    """
    Framework unified exception base class.

    Key design points:
    - StatusCode is the primary semantic identifier
    - Exception type represents control / recovery semantics
    - Message rendering is template-based and lazy-safe
    """

    status: StatusCode = StatusCode.ERROR
    recoverable: bool = False
    fatal: bool = False

    def __init__(
            self,
            status: StatusCode,
            *,
            msg: Optional[str] = None,
            details: Optional[Any] = None,
            cause: Optional[BaseException] = None,
            **kwargs: dict[str, Any],
    ):
        self.status = status
        self.code = self.status.code
        self.params = kwargs
        self.details = details
        self.cause = cause
        self.__cause__ = cause

        self._template_message = self._render_message()
        self.message = msg if msg else self._template_message
        super().__init__(self._template_message)

    def _render_message(self) -> str:
        """
        Render error message from StatusCode template.

        Never raise formatting exception outward.
        """
        try:
            return _format_template(self.status.errmsg, params=self.params)
        except Exception:
            return self.status.errmsg

    def to_dict(self) -> Dict[str, Any]:
        """
        Standard structured output for API / RPC / logging.
        """
        return {
            "code": self.code,
            "status": self.status.name,
            "message": self._template_message,
            "params": self.params,
            "raw_message": self.message,
            "details": self.details,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message if self.message else self._template_message}"

    @classmethod
    def _reconstruct(cls, status, msg, details, cause, params):
        params = params or {}
        return cls(status, msg=msg, details=details, cause=cause, **params)

    def __reduce__(self):
        return (
            self.__class__._reconstruct,
            (self.status, self.message, self.details, self.cause, self.params),
            None,
        )


class _SafeDict(dict):
    """
    dict subclass used for safe string formatting.
    If a key is missing, it inserts a placeholder '<missing:key>' instead of raising KeyError.
    """

    def __missing__(self,
                    key: str) -> str:
        return f"<missing:{key}>"


def _format_template(template: str,
                     params: Optional[Mapping[str, Any]] = None) -> str:
    """
    Safely format a template using provided params. Missing keys will be shown as '<missing:KEY>'.
    If template is None or empty, returns an empty string.
    """
    if not template:
        return ""
    safe = _SafeDict()
    if params:
        # copy to safe dict so unknown keys fallback works
        safe.update({k: (v if isinstance(v, str) else str(v)) for k, v in params.items()})
    try:
        return template.format_map(safe)
    except Exception:
        # As a last resort, return the raw template plus params summary
        try:
            return f"{template} (format error, params={dict(params) if params else {} })"
        except Exception:
            return template


# =======================
# Basic exception definitions
# =======================

class FrameworkError(BaseError):
    """
    Infrastructure / environment / dependency failures.
    Must abort current execution.
    """
    recoverable = False
    fatal = True


class ConfigurationError(FrameworkError):
    pass


class ValidationError(BaseError):
    """
    Constraint / validation / unsupported capability errors.
    Should NOT retry or replan.
    """
    recoverable = False
    fatal = False


class ExecutionError(BaseError):
    """
    Execution-time errors during workflow / agent / tool execution.
    Usually recoverable via retry / replan.
    """
    recoverable = True
    fatal = False


class ApplicationError(ExecutionError):
    pass


class ExternalServiceError(ExecutionError):
    pass


class ExternalDataError(ExecutionError):
    pass


class Termination(BaseError):
    """
    Non-error control-flow termination.
    Used for normal stop, cancellation, completion, etc.
    """
    recoverable = False
    fatal = False


# =========================
# Module domain exception definitions
# =========================

class RunnerTermination(Termination):
    def __init__(self, reason, status, **kwargs):
        super().__init__(status, **kwargs)
        self.reason = reason


class WorkflowError(ExecutionError):
    pass


class ComponentError(ExecutionError):
    pass


class AgentError(ExecutionError):
    pass


class RunnerError(ExecutionError):
    pass


class GraphError(ExecutionError):
    pass


class ModelError(ExecutionError):
    pass


class ToolError(ExecutionError):
    def __init__(
            self,
            status: StatusCode,
            *,
            msg: Optional[str] = None,
            details: Optional[Any] = None,
            cause: Optional[BaseException] = None,
            card: "BaseCard" = None,
            **kwargs: dict[str, Any],
    ):
        if card:
            self._card = deepcopy(card)
            if details is None:
                details = {}
            details = details.update({"card": self._card})
        else:
            self._card = None
        super().__init__(status=status, msg=msg, details=details, cause=cause, card=card, **kwargs)

    def card(self):
        return self._card


class ContextError(ExecutionError):
    pass


class ToolchainError(ExecutionError):
    pass


class SessionError(ExecutionError):
    pass


class SysOperationError(ExecutionError):
    pass


class GuardrailError(ValidationError):
    """
    Guardrail security check blocked error.

    Raised when guardrail detects a security risk and blocks the execution.
    Contains detailed risk information for logging and reporting.
    """
    pass


STATUS_TO_EXCEPTION = build_status_exception_map()


def build_error(
        status: StatusCode,
        *,
        msg: Optional[str] = None,
        details: Optional[Any] = None,
        cause: Optional[BaseException] = None,
        **kwargs,
) -> BaseError:
    """
    Build exception instance without raising.
    Useful for deferred throw or wrapping.
    """
    exc_cls = STATUS_TO_EXCEPTION.get(status, FrameworkError)
    return exc_cls(status, msg=msg, details=details, cause=cause, **kwargs)


def raise_error(
        status: StatusCode,
        *,
        msg: Optional[str] = None,
        details: Optional[Any] = None,
        cause: Optional[BaseException] = None,
        **kwargs,
) -> None:
    """
    Unified error raising entry.
    """
    raise build_error(status, msg=msg, details=details, cause=cause, **kwargs)


def system_error(
        status: StatusCode,
        *,
        cause: Optional[Exception] = None,
        **kwargs,
) -> None:
    raise FrameworkError(status, cause=cause, **kwargs)


def validate_error(
        status: StatusCode,
        *,
        cause: Optional[Exception] = None,
        **kwargs,
) -> None:
    raise ValidationError(status, cause=cause, **kwargs)


def terminate(
        status: StatusCode,
        **kwargs,
) -> None:
    raise Termination(status, **kwargs)
