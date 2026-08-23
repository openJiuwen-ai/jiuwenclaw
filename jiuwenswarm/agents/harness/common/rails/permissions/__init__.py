"""Security and permission integration for AgentServer.

This package hosts jiuwenswarm-side glue code for openjiuwen security rails,
owner-scoped policies, and persistence helpers.
"""

from __future__ import annotations

from importlib import import_module

_PACKAGE = "jiuwenswarm.agents.harness.common.rails.permissions"
_EXPORTS = {
    "ToolCapability": (f"{_PACKAGE}.tool_capabilities", "ToolCapability"),
    "ToolDecisionFacts": (
        f"{_PACKAGE}.tool_decision_facts",
        "ToolDecisionFacts",
    ),
    "build_tool_decision_facts": (
        f"{_PACKAGE}.tool_decision_facts",
        "build_tool_decision_facts",
    ),
    "classify_tool": (f"{_PACKAGE}.tool_capabilities", "classify_tool"),
    "normalize_tool_name": (
        f"{_PACKAGE}.tool_capabilities",
        "normalize_tool_name",
    ),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> object:
    """Resolve public permission facts without eager owner imports."""

    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Include lazy public exports in interactive module discovery."""

    return sorted(set(globals()) | set(__all__))
