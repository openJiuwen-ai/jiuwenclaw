"""Compatibility boundary for optional DeepResearch tools.

The implementation lives in :mod:`jiuwenclaw.agentserver.tools.deepresearch.tools`.
Alias the old module path to the implementation module so existing imports,
private test hooks, monkeypatches, and ``__file__`` checks keep their behavior.

DeepResearch is a product capability, but it must not become a hard dependency
for the base chat runtime.  HarmonyOS packages can temporarily lack the
DeepSearch SDK (for example during an incremental upgrade); in that case this
module preserves route push/reset semantics and exposes no DeepResearch tools.
"""

import contextvars
import logging
import sys

logger = logging.getLogger(__name__)

try:
    from jiuwenclaw.agentserver.tools.deepresearch import tools as _tools
except ImportError as error:
    logger.warning(
        "DeepResearch disabled because its runtime could not be imported: %s",
        error,
    )

    _fallback_route: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
        "jiuwenclaw_deepresearch_fallback_route",
        default=None,
    )

    def push_deepresearch_route(
        request_id: str,
        channel_id: str,
        session_id: str,
        *,
        service_id: str = "default",
        agent_id: str = "default",
    ) -> contextvars.Token:
        """Keep request scoping balanced while DeepResearch is unavailable."""
        return _fallback_route.set(
            {
                "request_id": request_id or "",
                "channel_id": channel_id or "",
                "session_id": session_id or "",
                "service_id": service_id or "default",
                "agent_id": agent_id or "default",
            }
        )

    def reset_deepresearch_route(token: contextvars.Token) -> None:
        _fallback_route.reset(token)

    def get_deepresearch_tools() -> list:
        """Disable only DeepResearch; base Agent tools remain available."""
        return []

    __all__ = [
        "get_deepresearch_tools",
        "push_deepresearch_route",
        "reset_deepresearch_route",
    ]
else:
    sys.modules[__name__] = _tools
