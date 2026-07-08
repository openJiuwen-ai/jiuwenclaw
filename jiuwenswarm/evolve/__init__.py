# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""JiuwenSwarm Self-Evolution Framework.

Provides a pluggable, dual-track evolution pipeline:

* **Component evolution track**: ``Trace -> Proposal -> Decision -> Apply``
  for Skills, Memory, and Tools.
* **Model evolution track**: Training Candidate data pool for future model
  fine-tuning (separate from the main pipeline).

Both tracks share OTEL trace data as the single source of truth.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jiuwenswarm.common.config import resolve_env_vars
from jiuwenswarm.evolve.registry import (
    Registry,
    apply_writers,
    decision_policies,
    proposal_generators,
    trace_samplers,
)

logger = logging.getLogger(__name__)

__all__ = [
    "Registry",
    "apply_writers",
    "decision_policies",
    "get_evolve_config",
    "proposal_generators",
    "trace_samplers",
]

# Cached merged config, loaded once per process.
_evolve_config: dict[str, Any] | None = None
# Flag to prevent repeated logger level initialization.
_evolve_logger_initialized: bool = False


def get_evolve_config() -> dict[str, Any]:
    """Return the merged evolution configuration.

    Loads ``jiuwenswarm/evolve/config.yaml`` as the base and merges
    ``config.yaml``'s ``evolve:`` section on top (main config wins).
    The result is cached in-process.

    Also sets the ``jiuwenswarm.evolve`` logger level from the ``log_level``
    field in the config (supports environment variable override).
    """
    global _evolve_config, _evolve_logger_initialized
    if _evolve_config is not None:
        return _evolve_config

    from ruamel.yaml import YAML

    yaml = YAML(typ="safe")

    # 1. Load evolve's own config.yaml
    _own_path = Path(__file__).parent / "config.yaml"
    own_cfg: dict[str, Any] = {}
    if _own_path.exists():
        own_cfg = yaml.load(_own_path.read_text(encoding="utf-8")) or {}

    # The file may wrap its contents under a top-level ``evolve:`` key
    # (mirroring the main config's ``evolve:`` section). All consumers of
    # get_evolve_config() read the UNWRAPPED form (e.g. ``.get("llm")``),
    # so unwrap a single-key ``{"evolve": {...}}`` wrapper here.
    if (
        len(own_cfg) == 1
        and "evolve" in own_cfg
        and isinstance(own_cfg["evolve"], dict)
    ):
        own_cfg = own_cfg["evolve"]

    # 1.5. Resolve environment variables (${VAR:-default} syntax)
    own_cfg = resolve_env_vars(own_cfg)

    # 2. Merge with main config's evolve: section (main wins)
    try:
        from jiuwenswarm.common.config import get_config

        main_cfg = get_config()
        main_evolve = main_cfg.get("evolve", {})
        if main_evolve:
            own_cfg = _deep_merge(own_cfg, main_evolve)
    except Exception as exc:
        logger.debug("Could not load main config evolve section: %s", exc)

    _evolve_config = own_cfg

    # 3. Set jiuwenswarm.evolve logger level from config
    if not _evolve_logger_initialized:
        _evolve_logger_initialized = True
        _apply_evolve_log_level(_evolve_config)

    return _evolve_config


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*.  *override* wins."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _apply_evolve_log_level(cfg: dict[str, Any]) -> None:
    """Set the ``jiuwenswarm.evolve`` logger level from config.

    Uses ``log_level`` from evolve config (default INFO).
    Only the ``jiuwenswarm.evolve`` hierarchy is affected;
    other modules (channel, agent_server, gateway) are untouched.
    """
    level_str = str(cfg.get("log_level", "INFO"))
    level = _parse_evolve_log_level(level_str)
    evolve_root = logging.getLogger("jiuwenswarm.evolve")
    evolve_root.setLevel(level)
    logger.debug("evolve logger level set to %s (%d)", level_str, level)


def _parse_evolve_log_level(name: str, default: int = logging.INFO) -> int:
    """Parse level name to logging module constant."""
    if not name or not isinstance(name, str):
        return default
    return getattr(logging, name.strip().upper(), default)

