"""Helpers for constructing the Celia MCP subprocess environment."""

from __future__ import annotations

from collections.abc import Mapping

from .config import CeliaConfig


def build_child_env(
    config: CeliaConfig,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return the sanitized environment passed to ``celia_memory_mcp_server``."""

    return config.child_env(base)


__all__ = ["build_child_env"]
