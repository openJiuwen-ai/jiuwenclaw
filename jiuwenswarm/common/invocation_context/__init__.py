"""Explicit, invocation-scoped context propagation for JiuWenSwarm.

The context is deliberately kept in a small Jiuwen-owned package so that it
can cross the persistent DeepAgent boundary through ``RunContext.extra``
without changing agent-core or either RPC wire schema.
"""

from .codec import (
    INVOCATION_CONTEXT_EXTRA_KEY,
    attach_invocation_context,
    invocation_context_from_dict,
    invocation_context_to_dict,
)
from .adapters import build_device_command_context_from_invocation
from .models import (
    INVOCATION_CONTEXT_VERSION,
    InvocationContext,
    XiaoyiInvocationContext,
)
from .runtime import (
    get_current_invocation_context,
    reset_current_invocation_context,
    set_current_invocation_context,
)

__all__ = [
    "INVOCATION_CONTEXT_EXTRA_KEY",
    "INVOCATION_CONTEXT_VERSION",
    "InvocationContext",
    "XiaoyiInvocationContext",
    "attach_invocation_context",
    "build_device_command_context_from_invocation",
    "get_current_invocation_context",
    "invocation_context_from_dict",
    "invocation_context_to_dict",
    "reset_current_invocation_context",
    "set_current_invocation_context",
]
