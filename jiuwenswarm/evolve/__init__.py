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


def get_evolve_config() -> dict[str, Any]:
    """Return the merged evolution configuration.

    Loads ``jiuwenswarm/evolve/config.yaml`` as the base and merges
    ``config.yaml``'s ``evolve:`` section on top (main config wins).
    The result is cached in-process.
    """
    global _evolve_config
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

