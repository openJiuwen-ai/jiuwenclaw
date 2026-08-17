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
    trace_context_from_dict,
    trace_context_to_dict,
)
from .models import (
    INVOCATION_CONTEXT_VERSION,
    TRACE_CONTEXT_VERSION,
    InvocationContext,
    TraceContext,
)
from .runtime import (
    get_current_invocation_context,
    reset_current_invocation_context,
    set_current_invocation_context,
)

TRACE_CONTEXT_METADATA_KEY = "jiuwenswarm_trace_context"
TRACE_HEADER_EXPORTER_METADATA_KEY = "jiuwenswarm_trace_header_exporter"

__all__ = [
    "INVOCATION_CONTEXT_EXTRA_KEY",
    "INVOCATION_CONTEXT_VERSION",
    "TRACE_CONTEXT_VERSION",
    "TRACE_CONTEXT_METADATA_KEY",
    "TRACE_HEADER_EXPORTER_METADATA_KEY",
    "InvocationContext",
    "TraceContext",
    "attach_invocation_context",
    "get_current_invocation_context",
    "invocation_context_from_dict",
    "invocation_context_to_dict",
    "reset_current_invocation_context",
    "set_current_invocation_context",
    "trace_context_from_dict",
    "trace_context_to_dict",
]
