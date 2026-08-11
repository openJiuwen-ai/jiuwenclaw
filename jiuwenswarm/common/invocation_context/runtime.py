"""Runtime ContextVar for the *actual* Tool execution task."""

from __future__ import annotations

from contextvars import ContextVar, Token

from .models import InvocationContext


_CURRENT_INVOCATION_CONTEXT: ContextVar[InvocationContext | None] = ContextVar(
    "current_jiuwenswarm_invocation_context",
    default=None,
)


def set_current_invocation_context(context: InvocationContext) -> Token:
    if not isinstance(context, InvocationContext):
        raise TypeError("context must be an InvocationContext")
    return _CURRENT_INVOCATION_CONTEXT.set(context)


def get_current_invocation_context() -> InvocationContext | None:
    return _CURRENT_INVOCATION_CONTEXT.get()


def reset_current_invocation_context(token: Token) -> None:
    _CURRENT_INVOCATION_CONTEXT.reset(token)
